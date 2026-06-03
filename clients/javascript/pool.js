'use strict';

/**
 * PulseDB connection pool for Node.js.
 *
 * Manages a pool of reusable PulseDB connections — essential for servers
 * handling many concurrent requests.
 *
 * @example
 * ```js
 * const { ConnectionPool } = require('./pool');
 *
 * const pool = new ConnectionPool({
 *   host: '127.0.0.1', port: 7878,
 *   minSize: 2, maxSize: 10,
 *   username: process.env.PULSEDB_USER,
 *   password: process.env.PULSEDB_PASSWORD,
 * });
 *
 * // Acquire → use → auto-release
 * const result = await pool.withConnection(async (db) => {
 *   return db.query('GET users LIMIT 10');
 * });
 *
 * // Or manually:
 * const db = await pool.acquire();
 * try {
 *   await db.query('PUT users { id: 1, name: "Alice" }');
 * } finally {
 *   pool.release(db);
 * }
 *
 * await pool.close();
 * ```
 */

const { PulseDB } = require('./index');

class ConnectionPool {
  /**
   * @param {object} opts
   * @param {string}  [opts.host='127.0.0.1']
   * @param {number}  [opts.port=7878]
   * @param {number}  [opts.minSize=1]      Connections opened at construction
   * @param {number}  [opts.maxSize=10]     Hard cap on total connections
   * @param {string}  [opts.username]
   * @param {string}  [opts.password]
   * @param {boolean} [opts.tls=false]
   * @param {boolean} [opts.tlsNoVerify=false]
   * @param {number}  [opts.acquireTimeout=30000]  ms to wait for a free slot
   */
  constructor(opts = {}) {
    this._host           = opts.host           || '127.0.0.1';
    this._port           = opts.port           || 7878;
    this._minSize        = opts.minSize        || 1;
    this._maxSize        = opts.maxSize        || 10;
    this._username       = opts.username       || null;
    this._password       = opts.password       || null;
    this._tls            = opts.tls            || false;
    this._tlsNoVerify    = opts.tlsNoVerify    || false;
    this._acquireTimeout = opts.acquireTimeout || 30_000;

    this._idle    = [];   // available connections
    this._open    = 0;    // total connections (idle + in-use)
    this._waiting = [];   // {resolve, reject, timer} queue
    this._closed  = false;
    this._ready   = this._init();
  }

  // ── Initialisation ────────────────────────────────────────────────────

  async _init() {
    for (let i = 0; i < this._minSize; i++) {
      const c = await this._dial();
      this._idle.push(c);
      this._open++;
    }
  }

  /** Wait for the pool to be ready (minSize connections open). */
  async ready() { return this._ready; }

  // ── Public API ────────────────────────────────────────────────────────

  /**
   * Acquire a connection from the pool.
   * Resolves immediately if one is idle, or waits up to `acquireTimeout` ms.
   * @returns {Promise<PulseDB>}
   */
  acquire() {
    if (this._closed) return Promise.reject(new Error('pool is closed'));

    // Fast path: idle connection available
    if (this._idle.length > 0) {
      return Promise.resolve(this._idle.pop());
    }

    // Can open a new connection
    if (this._open < this._maxSize) {
      this._open++;
      return this._dial().catch(err => {
        this._open--;
        throw err;
      });
    }

    // At cap — queue the request
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const idx = this._waiting.findIndex(w => w.resolve === resolve);
        if (idx !== -1) this._waiting.splice(idx, 1);
        reject(new Error(
          `connection pool exhausted (max=${this._maxSize}) — ` +
          `increase maxSize or acquireTimeout`
        ));
      }, this._acquireTimeout);

      this._waiting.push({ resolve, reject, timer });
    });
  }

  /**
   * Return a connection to the pool.
   * @param {PulseDB} conn
   */
  release(conn) {
    if (!conn) return;

    // Serve any waiting acquirer first
    if (this._waiting.length > 0) {
      const { resolve, timer } = this._waiting.shift();
      clearTimeout(timer);
      resolve(conn);
      return;
    }

    // Verify the connection is still alive
    conn.query('SHOW TABLES').then(() => {
      this._idle.push(conn);
    }).catch(() => {
      conn.close();
      this._open--;
    });
  }

  /**
   * Run `fn(conn)` with an auto-released connection.
   * @template T
   * @param {function(PulseDB): Promise<T>} fn
   * @returns {Promise<T>}
   */
  async withConnection(fn) {
    const conn = await this.acquire();
    try {
      return await fn(conn);
    } finally {
      this.release(conn);
    }
  }

  /**
   * Close all connections and shut down the pool.
   */
  async close() {
    this._closed = true;
    for (const { reject, timer } of this._waiting) {
      clearTimeout(timer);
      reject(new Error('pool closed'));
    }
    this._waiting = [];
    for (const conn of this._idle) { conn.close(); }
    this._idle = [];
    this._open = 0;
  }

  /** Current pool statistics. */
  get stats() {
    return {
      idle:   this._idle.length,
      total:  this._open,
      waiting: this._waiting.length,
    };
  }

  // ── Internal ──────────────────────────────────────────────────────────

  async _dial() {
    const conn = await PulseDB.connect({
      host: this._host, port: this._port,
      tls: this._tls, tlsNoVerify: this._tlsNoVerify,
    });
    if (this._username && this._password) {
      await conn.auth(this._username, this._password);
    }
    return conn;
  }
}

module.exports = { ConnectionPool };
