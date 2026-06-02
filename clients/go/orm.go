// Package pulsedb provides the Go client and ORM for PulseDB.
//
// ORM usage:
//
//	type User struct {
//	    ID     int     `pulsedb:"id,primary_key"`
//	    Name   string  `pulsedb:"name"`
//	    Age    int     `pulsedb:"age"`
//	    Active bool    `pulsedb:"active"`
//	    Score  float64 `pulsedb:"score"`
//	}
//
//	db, _ := pulsedb.Connect("127.0.0.1:7878")
//	orm    := pulsedb.NewORM(db)
//
//	orm.CreateTable(&User{})
//
//	orm.Create(&User{ID: 1, Name: "Alice", Age: 30, Active: true})
//
//	var users []User
//	orm.Where("age >= 18 AND active = true").OrderBy("age", false).Limit(10).Find(&users)
//
//	var alice User
//	orm.FindByPK(&alice, 1)
//
//	orm.Where("id = 1").Update(&User{}, map[string]interface{}{"age": 31})
//	orm.Where("active = false").Delete(&User{})

package pulsedb

import (
	"fmt"
	"reflect"
	"strconv"
	"strings"
)

// ── Field metadata ────────────────────────────────────────────────────────────

type fieldInfo struct {
	Column     string
	PrimaryKey bool
	GoField    string
	Kind       reflect.Kind
}

// parseFields reflects on a struct pointer and reads `pulsedb:"col[,primary_key]"` tags.
func parseFields(model interface{}) ([]fieldInfo, string) {
	t := reflect.TypeOf(model)
	if t.Kind() == reflect.Ptr {
		t = t.Elem()
	}
	var fields []fieldInfo
	pkCol := ""
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		tag := f.Tag.Get("pulsedb")
		if tag == "" || tag == "-" {
			continue
		}
		parts := strings.SplitN(tag, ",", 2)
		col := parts[0]
		isPK := len(parts) > 1 && strings.Contains(parts[1], "primary_key")
		fields = append(fields, fieldInfo{
			Column:     col,
			PrimaryKey: isPK,
			GoField:    f.Name,
			Kind:       f.Type.Kind(),
		})
		if isPK {
			pkCol = col
		}
	}
	_ = pkCol
	return fields, pkCol
}

// pulseqlType maps Go kinds to PulseQL type names.
func pulseqlType(k reflect.Kind) string {
	switch k {
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64,
		reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return "int"
	case reflect.Float32, reflect.Float64:
		return "float"
	case reflect.Bool:
		return "bool"
	case reflect.String:
		return "text"
	case reflect.Slice: // []float32 / []float64 → vector
		return "vector"
	default:
		return "json"
	}
}

// toLiteral serialises a reflect.Value to a PulseQL literal string.
func toLiteral(v reflect.Value) string {
	if !v.IsValid() {
		return "null"
	}
	switch v.Kind() {
	case reflect.Ptr, reflect.Interface:
		if v.IsNil() {
			return "null"
		}
		return toLiteral(v.Elem())
	case reflect.Bool:
		if v.Bool() {
			return "true"
		}
		return "false"
	case reflect.String:
		s := strings.ReplaceAll(v.String(), `\`, `\\`)
		s = strings.ReplaceAll(s, `"`, `\"`)
		return `"` + s + `"`
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return strconv.FormatInt(v.Int(), 10)
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return strconv.FormatUint(v.Uint(), 10)
	case reflect.Float32, reflect.Float64:
		return strconv.FormatFloat(v.Float(), 'f', 6, 64)
	case reflect.Slice:
		if v.IsNil() {
			return "null"
		}
		// []float32 or []float64 → vector literal
		parts := make([]string, v.Len())
		for i := 0; i < v.Len(); i++ {
			parts[i] = toLiteral(v.Index(i))
		}
		return "[" + strings.Join(parts, ", ") + "]"
	default:
		return fmt.Sprintf(`"%v"`, v.Interface())
	}
}

// setField deserialises a raw interface{} value into a struct field.
func setField(target reflect.Value, raw interface{}) {
	if raw == nil {
		return
	}
	switch target.Kind() {
	case reflect.String:
		target.SetString(fmt.Sprintf("%v", raw))
	case reflect.Bool:
		switch v := raw.(type) {
		case bool:
			target.SetBool(v)
		case string:
			target.SetBool(v == "true" || v == "1")
		case float64:
			target.SetBool(v != 0)
		}
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		switch v := raw.(type) {
		case float64:
			target.SetInt(int64(v))
		case int:
			target.SetInt(int64(v))
		case string:
			if n, err := strconv.ParseInt(v, 10, 64); err == nil {
				target.SetInt(n)
			}
		}
	case reflect.Float32, reflect.Float64:
		switch v := raw.(type) {
		case float64:
			target.SetFloat(v)
		case string:
			if f, err := strconv.ParseFloat(v, 64); err == nil {
				target.SetFloat(f)
			}
		}
	case reflect.Slice:
		// vector field: raw comes as []interface{} from JSON
		if arr, ok := raw.([]interface{}); ok {
			sl := reflect.MakeSlice(target.Type(), len(arr), len(arr))
			for i, elem := range arr {
				if f, ok := elem.(float64); ok {
					switch target.Type().Elem().Kind() {
					case reflect.Float32:
						sl.Index(i).SetFloat(float64(float32(f)))
					case reflect.Float64:
						sl.Index(i).SetFloat(f)
					}
				}
			}
			target.Set(sl)
		}
	}
}

// tableName derives the table name from a struct type.
// Override by implementing TableName() string on the model.
func tableName(model interface{}) string {
	type tabler interface{ TableName() string }
	if t, ok := model.(tabler); ok {
		return t.TableName()
	}
	t := reflect.TypeOf(model)
	if t.Kind() == reflect.Ptr {
		t = t.Elem()
	}
	return strings.ToLower(t.Name()) + "s"
}

// ── ORM ───────────────────────────────────────────────────────────────────────

// ORM wraps a PulseDB *Client and provides a high-level query API.
type ORM struct {
	client *Client
}

// NewORM creates an ORM backed by an existing PulseDB connection.
func NewORM(c *Client) *ORM {
	return &ORM{client: c}
}

// Q starts a QueryBuilder for the given model (pass a pointer to a zero struct).
//
//	var user User
//	orm.Q(&user).Where("age >= 18").Limit(10).Find(&[]User{})
func (o *ORM) Q(model interface{}) *QueryBuilder {
	return &QueryBuilder{
		orm:   o,
		model: model,
		table: tableName(model),
	}
}

// ── Schema management ─────────────────────────────────────────────────────────

// CreateTable generates and executes a MAKE TABLE statement from struct tags.
func (o *ORM) CreateTable(model interface{}) error {
	fields, _ := parseFields(model)
	table := tableName(model)
	cols := make([]string, 0, len(fields))
	for _, f := range fields {
		typ := pulseqlType(f.Kind)
		pk := ""
		if f.PrimaryKey {
			pk = " PRIMARY KEY"
		}
		cols = append(cols, fmt.Sprintf("%s %s%s", f.Column, typ, pk))
	}
	_, err := o.client.Query(fmt.Sprintf("MAKE TABLE %s (%s)", table, strings.Join(cols, ", ")))
	if err != nil {
		if strings.Contains(err.Error(), "already exists") {
			return nil // idempotent
		}
		return err
	}
	return nil
}

// DropTable removes the table for the given model.
func (o *ORM) DropTable(model interface{}) error {
	_, err := o.client.Query("DROP TABLE " + tableName(model))
	return err
}

// CreateIndex creates an index on one or more columns.
func (o *ORM) CreateIndex(model interface{}, columns ...string) error {
	table := tableName(model)
	for _, col := range columns {
		if _, err := o.client.Query(
			fmt.Sprintf("MAKE INDEX ON %s (%s)", table, col),
		); err != nil {
			return err
		}
	}
	return nil
}

// ── Write ─────────────────────────────────────────────────────────────────────

// Create inserts a struct as a new row (upsert).
func (o *ORM) Create(model interface{}) error {
	return o.Q(model).insert(model)
}

// Save is an alias for Create (PUT / upsert).
func (o *ORM) Save(model interface{}) error {
	return o.Create(model)
}

// ── Read ──────────────────────────────────────────────────────────────────────

// Find populates dest (pointer to []MyStruct) with all rows matching the
// query built from optional Where/OrderBy/Limit calls.
//
//	var users []User
//	orm.Where(&User{}, "age >= 18").Find(&users)
func (o *ORM) Find(dest interface{}, model interface{}) error {
	return o.Q(model).Find(dest)
}

// FindByPK fetches a single row by primary key value.
//
//	var user User
//	orm.FindByPK(&user, 1)
func (o *ORM) FindByPK(dest interface{}, pk interface{}) error {
	fields, pkCol := parseFields(dest)
	if pkCol == "" {
		return fmt.Errorf("no primary_key tag found on %T", dest)
	}
	_ = fields
	table := tableName(dest)
	pkLit := toLiteral(reflect.ValueOf(pk))
	res, err := o.client.Query(
		fmt.Sprintf("GET %s WHERE %s = %s LIMIT 1", table, pkCol, pkLit),
	)
	if err != nil {
		return err
	}
	if len(res.Rows) == 0 {
		return fmt.Errorf("record not found")
	}
	hydrate(dest, res.Columns, res.Rows[0])
	return nil
}

// ── Transaction ───────────────────────────────────────────────────────────────

// Transaction wraps fn in a BEGIN / COMMIT (or ROLLBACK on error).
func (o *ORM) Transaction(fn func(*ORM) error) error {
	if _, err := o.client.Query("BEGIN"); err != nil {
		return err
	}
	if err := fn(o); err != nil {
		o.client.Query("ROLLBACK") //nolint:errcheck
		return err
	}
	_, err := o.client.Query("COMMIT")
	return err
}

// ── QueryBuilder ──────────────────────────────────────────────────────────────

// QueryBuilder is a chainable query object returned by ORM.Q().
type QueryBuilder struct {
	orm     *ORM
	model   interface{}
	table   string
	wheres  []string
	orders  []string
	limitN  int
}

// Where appends a raw PulseQL WHERE condition (AND-joined).
//
//	orm.Q(&User{}).Where("age >= 18").Where("active = true").Find(&users)
func (qb *QueryBuilder) Where(condition string) *QueryBuilder {
	c := qb.clone()
	c.wheres = append(c.wheres, condition)
	return c
}

// OrderBy adds an ORDER BY clause. Set desc=true for descending.
func (qb *QueryBuilder) OrderBy(column string, desc bool) *QueryBuilder {
	c := qb.clone()
	dir := "ASC"
	if desc {
		dir = "DESC"
	}
	c.orders = append(c.orders, column+" "+dir)
	return c
}

// Limit adds a LIMIT clause.
func (qb *QueryBuilder) Limit(n int) *QueryBuilder {
	c := qb.clone()
	c.limitN = n
	return c
}

// Find executes the query and populates dest (pointer to slice).
func (qb *QueryBuilder) Find(dest interface{}) error {
	stmt := "GET " + qb.table + qb.whereClause() + qb.orderClause() + qb.limitClause()
	res, err := qb.orm.client.Query(stmt)
	if err != nil {
		return err
	}

	destVal := reflect.ValueOf(dest).Elem()
	elemType := destVal.Type().Elem()

	for _, row := range res.Rows {
		elem := reflect.New(elemType).Elem()
		hydrateValue(elem, res.Columns, row)
		destVal.Set(reflect.Append(destVal, elem))
	}
	return nil
}

// First returns the first matching row.
func (qb *QueryBuilder) First(dest interface{}) error {
	stmt := "GET " + qb.table + qb.whereClause() + qb.orderClause() + " LIMIT 1"
	res, err := qb.orm.client.Query(stmt)
	if err != nil {
		return err
	}
	if len(res.Rows) == 0 {
		return fmt.Errorf("record not found")
	}
	hydrate(dest, res.Columns, res.Rows[0])
	return nil
}

// Count returns the number of matching rows.
func (qb *QueryBuilder) Count() (int, error) {
	stmt := "GET " + qb.table + qb.whereClause()
	res, err := qb.orm.client.Query(stmt)
	if err != nil {
		return 0, err
	}
	return len(res.Rows), nil
}

// Update updates fields matching the current WHERE clause.
//
//	orm.Q(&User{}).Where("active = false").Update(map[string]interface{}{"active": true})
func (qb *QueryBuilder) Update(values map[string]interface{}) error {
	fields, _ := parseFields(qb.model)
	fieldMap := make(map[string]fieldInfo, len(fields))
	for _, f := range fields {
		fieldMap[f.Column] = f
	}
	parts := make([]string, 0, len(values))
	for col, val := range values {
		lit := toLiteral(reflect.ValueOf(val))
		parts = append(parts, fmt.Sprintf("%s: %s", col, lit))
	}
	stmt := fmt.Sprintf("SET %s { %s }%s",
		qb.table, strings.Join(parts, ", "), qb.whereClause())
	_, err := qb.orm.client.Query(stmt)
	return err
}

// Delete deletes rows matching the current WHERE clause.
func (qb *QueryBuilder) Delete() error {
	_, err := qb.orm.client.Query("DEL " + qb.table + qb.whereClause())
	return err
}

// Similar executes a SIMILAR (vector cosine search) query.
func (qb *QueryBuilder) Similar(column string, vector []float64, k int) (*Result, error) {
	parts := make([]string, len(vector))
	for i, v := range vector {
		parts[i] = strconv.FormatFloat(v, 'f', 6, 64)
	}
	stmt := fmt.Sprintf("SIMILAR %s ON %s TO [%s] LIMIT %d",
		qb.table, column, strings.Join(parts, ", "), k)
	return qb.orm.client.Query(stmt)
}

// Fuzzy executes a FIND (trigram fuzzy text search) query.
func (qb *QueryBuilder) Fuzzy(column, pattern string, limit int) (*Result, error) {
	escaped := strings.ReplaceAll(pattern, `"`, `\"`)
	stmt := fmt.Sprintf(`FIND %s WHERE %s ~ "%s" LIMIT %d`,
		qb.table, column, escaped, limit)
	return qb.orm.client.Query(stmt)
}

// ── Internal ──────────────────────────────────────────────────────────────────

func (qb *QueryBuilder) insert(model interface{}) error {
	fields, _ := parseFields(model)
	v := reflect.ValueOf(model)
	if v.Kind() == reflect.Ptr {
		v = v.Elem()
	}
	parts := make([]string, 0, len(fields))
	for _, f := range fields {
		fv := v.FieldByName(f.GoField)
		parts = append(parts, fmt.Sprintf("%s: %s", f.Column, toLiteral(fv)))
	}
	_, err := qb.orm.client.Query(
		fmt.Sprintf("PUT %s { %s }", qb.table, strings.Join(parts, ", ")),
	)
	return err
}

func (qb *QueryBuilder) clone() *QueryBuilder {
	c := *qb
	c.wheres = append([]string(nil), qb.wheres...)
	c.orders = append([]string(nil), qb.orders...)
	return &c
}

func (qb *QueryBuilder) whereClause() string {
	if len(qb.wheres) == 0 {
		return ""
	}
	return " WHERE " + strings.Join(qb.wheres, " AND ")
}

func (qb *QueryBuilder) orderClause() string {
	if len(qb.orders) == 0 {
		return ""
	}
	return " ORDER BY " + strings.Join(qb.orders, ", ")
}

func (qb *QueryBuilder) limitClause() string {
	if qb.limitN <= 0 {
		return ""
	}
	return fmt.Sprintf(" LIMIT %d", qb.limitN)
}

// hydrate populates a struct pointer from column names + raw values.
func hydrate(dest interface{}, columns []string, values []interface{}) {
	v := reflect.ValueOf(dest)
	if v.Kind() == reflect.Ptr {
		v = v.Elem()
	}
	hydrateValue(v, columns, values)
}

func hydrateValue(v reflect.Value, columns []string, values []interface{}) {
	fields, _ := parseFields(v.Addr().Interface())
	colIndex := make(map[string]int, len(columns))
	for i, c := range columns {
		colIndex[c] = i
	}
	for _, f := range fields {
		idx, ok := colIndex[f.Column]
		if !ok {
			continue
		}
		fv := v.FieldByName(f.GoField)
		if fv.IsValid() && fv.CanSet() {
			setField(fv, values[idx])
		}
	}
}
