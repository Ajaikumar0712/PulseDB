// Package pulsedb is the Go client for PulseDB.
// It connects over TCP and speaks PulseQL via line-delimited JSON.
package pulsedb

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"sync"
)

// Error returned when the server sends an error response.
type PulseDBError struct {
	Message string
}

func (e *PulseDBError) Error() string { return "pulsedb: " + e.Message }

// Row is a single result row. Access column values by name via Get or Fields.
type Row struct {
	columns []string
	values  []interface{}
}

// Get returns the value of the named column, or nil if not found.
func (r *Row) Get(column string) interface{} {
	for i, c := range r.columns {
		if c == column {
			return r.values[i]
		}
	}
	return nil
}

// Fields returns the row as a map.
func (r *Row) Fields() map[string]interface{} {
	m := make(map[string]interface{}, len(r.columns))
	for i, c := range r.columns {
		m[c] = r.values[i]
	}
	return m
}

// Result holds the response from a PulseQL query.
type Result struct {
	OK       bool
	Error    string
	Message  string
	Affected int
	Elapsed  int64
	Columns  []string
	Rows     []*Row
}

func parseResult(raw map[string]interface{}) *Result {
	r := &Result{OK: true}
	if e, ok := raw["error"].(string); ok {
		r.OK = false
		r.Error = e
	}
	if m, ok := raw["message"].(string); ok {
		r.Message = m
	}
	if a, ok := raw["affected"].(float64); ok {
		r.Affected = int(a)
	}
	if e, ok := raw["elapsed_ms"].(float64); ok {
		r.Elapsed = int64(e)
	}
	if cols, ok := raw["columns"].([]interface{}); ok {
		for _, c := range cols {
			if s, ok := c.(string); ok {
				r.Columns = append(r.Columns, s)
			}
		}
	}
	if rows, ok := raw["rows"].([]interface{}); ok {
		for _, rowRaw := range rows {
			if vals, ok := rowRaw.([]interface{}); ok {
				r.Rows = append(r.Rows, &Row{columns: r.Columns, values: vals})
			}
		}
	}
	return r
}

// Client is a thread-safe PulseDB connection.
type Client struct {
	conn    net.Conn
	scanner *bufio.Scanner
	writer  *bufio.Writer
	mu      sync.Mutex
}

// Connect opens a TCP connection to the given address (e.g. "127.0.0.1:7878").
func Connect(addr string) (*Client, error) {
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		return nil, fmt.Errorf("pulsedb: connect %s: %w", addr, err)
	}
	return &Client{
		conn:    conn,
		scanner: bufio.NewScanner(conn),
		writer:  bufio.NewWriter(conn),
	}, nil
}

// Auth authenticates the session.
func (c *Client) Auth(username, password string) error {
	_, err := c.Query(fmt.Sprintf("AUTH '%s' '%s'", username, password))
	return err
}

// Query sends a PulseQL statement and returns the parsed result.
func (c *Client) Query(q string) (*Result, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	payload, _ := json.Marshal(map[string]string{"query": q})
	c.writer.Write(payload)
	c.writer.WriteByte('\n')
	if err := c.writer.Flush(); err != nil {
		return nil, fmt.Errorf("pulsedb: write: %w", err)
	}

	if !c.scanner.Scan() {
		if err := c.scanner.Err(); err != nil {
			return nil, fmt.Errorf("pulsedb: read: %w", err)
		}
		return nil, fmt.Errorf("pulsedb: connection closed")
	}

	var raw map[string]interface{}
	if err := json.Unmarshal(c.scanner.Bytes(), &raw); err != nil {
		return nil, fmt.Errorf("pulsedb: parse response: %w", err)
	}

	result := parseResult(raw)
	if !result.OK {
		return nil, &PulseDBError{Message: result.Error}
	}
	return result, nil
}

// Close closes the underlying TCP connection.
func (c *Client) Close() error { return c.conn.Close() }
