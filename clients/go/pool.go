package pulsedb

import (
	"crypto/tls"
	"fmt"
	"sync"
)

// Pool is a thread-safe connection pool for PulseDB.
//
// Usage:
//
//	pool, err := pulsedb.NewPool(pulsedb.PoolConfig{
//	    Addr:     "127.0.0.1:7878",
//	    MinSize:  2,
//	    MaxSize:  10,
//	    Username: os.Getenv("PULSEDB_USER"),
//	    Password: os.Getenv("PULSEDB_PASSWORD"),
//	})
//	if err != nil { log.Fatal(err) }
//	defer pool.Close()
//
//	conn, err := pool.Acquire()
//	if err != nil { ... }
//	defer pool.Release(conn)
//	conn.Query("GET users")
type Pool struct {
	cfg  PoolConfig
	idle chan *Client
	mu   sync.Mutex
	open int
}

// PoolConfig holds the settings for a connection pool.
type PoolConfig struct {
	// Addr is the server address, e.g. "127.0.0.1:7878".
	Addr string
	// MinSize is the number of connections opened eagerly.
	MinSize int
	// MaxSize is the hard cap on total connections.
	MaxSize int
	// Username and Password for authentication (leave empty for --no-auth servers).
	Username string
	Password string
	// TLSConfig enables TLS when non-nil.
	// Use &tls.Config{InsecureSkipVerify: true} for self-signed certs in dev.
	TLSConfig *tls.Config
}

// NewPool creates a pool and opens MinSize connections eagerly.
func NewPool(cfg PoolConfig) (*Pool, error) {
	if cfg.MaxSize <= 0 {
		cfg.MaxSize = 10
	}
	if cfg.MinSize <= 0 {
		cfg.MinSize = 1
	}
	if cfg.MinSize > cfg.MaxSize {
		cfg.MinSize = cfg.MaxSize
	}

	p := &Pool{
		cfg:  cfg,
		idle: make(chan *Client, cfg.MaxSize),
	}

	for i := 0; i < cfg.MinSize; i++ {
		c, err := p.dial()
		if err != nil {
			p.Close()
			return nil, fmt.Errorf("pulsedb pool: initial connect: %w", err)
		}
		p.idle <- c
		p.mu.Lock()
		p.open++
		p.mu.Unlock()
	}
	return p, nil
}

// Acquire checks out a connection. Call Release when done.
func (p *Pool) Acquire() (*Client, error) {
	select {
	case c := <-p.idle:
		if p.ping(c) {
			return c, nil
		}
		// Stale — replace it
		c.Close()
		p.mu.Lock()
		p.open--
		p.mu.Unlock()
	default:
	}

	// No idle connection — open a new one if under the cap
	p.mu.Lock()
	if p.open < p.cfg.MaxSize {
		p.open++
		p.mu.Unlock()
		c, err := p.dial()
		if err != nil {
			p.mu.Lock()
			p.open--
			p.mu.Unlock()
			return nil, fmt.Errorf("pulsedb pool: connect: %w", err)
		}
		return c, nil
	}
	p.mu.Unlock()

	// At cap — wait for a connection to be released
	c := <-p.idle
	if !p.ping(c) {
		c.Close()
		p.mu.Lock()
		p.open--
		p.mu.Unlock()
		return p.Acquire() // retry once
	}
	return c, nil
}

// Release returns a connection to the pool.
func (p *Pool) Release(c *Client) {
	if c == nil {
		return
	}
	select {
	case p.idle <- c:
	default:
		// Pool is full — close the excess connection
		c.Close()
		p.mu.Lock()
		p.open--
		p.mu.Unlock()
	}
}

// Close shuts down every connection in the pool.
func (p *Pool) Close() {
	close(p.idle)
	for c := range p.idle {
		c.Close()
	}
}

// Stats returns (idle, total) connection counts.
func (p *Pool) Stats() (idle, total int) {
	p.mu.Lock()
	total = p.open
	p.mu.Unlock()
	idle = len(p.idle)
	return
}

func (p *Pool) dial() (*Client, error) {
	var (
		c   *Client
		err error
	)
	if p.cfg.TLSConfig != nil {
		c, err = ConnectTLS(p.cfg.Addr, p.cfg.TLSConfig)
	} else {
		c, err = Connect(p.cfg.Addr)
	}
	if err != nil {
		return nil, err
	}
	if p.cfg.Username != "" && p.cfg.Password != "" {
		if authErr := c.Auth(p.cfg.Username, p.cfg.Password); authErr != nil {
			c.Close()
			return nil, authErr
		}
	}
	return c, nil
}

func (p *Pool) ping(c *Client) bool {
	_, err := c.Query("SHOW TABLES")
	return err == nil
}
