# pulsedb — Go client

```bash
go get github.com/pulsedb/client-go
```

## Quick start

```go
package main

import (
    "fmt"
    "log"

    pulsedb "github.com/pulsedb/client-go"
)

func main() {
    db, err := pulsedb.Connect("127.0.0.1:7878")
    if err != nil {
        log.Fatal(err)
    }
    defer db.Close()

    db.Query(`MAKE TABLE users (id int, name text, score float)`)
    db.Query(`PUT users (1, "Alice", 9.5)`)
    db.Query(`PUT users (2, "Bob",   7.2)`)

    result, err := db.Query("GET users ORDER BY score DESC")
    if err != nil {
        log.Fatal(err)
    }

    for _, row := range result.Rows {
        fmt.Println(row.Get("id"), row.Get("name"), row.Get("score"))
    }
}
```

## Transactions

```go
db.Query("BEGIN")
db.Query(`PUT orders (101, "shipped")`)
db.Query("COMMIT")
```

## Authentication

```go
if err := db.Auth("admin", "secret"); err != nil {
    log.Fatal(err)
}
```
