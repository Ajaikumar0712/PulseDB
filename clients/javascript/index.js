'use strict';

const net = require('net');

class PulseDBError extends Error {
  constructor(message) {
    super(message);
    this.name = 'PulseDBError';
  }
}

class Row {
  constructor(columns, values) {
    columns.forEach((col, i) => { this[col] = values[i]; });
    Object.defineProperty(this, '_columns', { value: columns, enumerable: false });
  }

  toObject() {
    return Object.fromEntries(this._columns.map(c => [c, this[c]]));
  }
}

class Result {
  constructor(raw) {
    this._raw = raw;
    this.ok = !raw.error;
    this.error = raw.error || null;
    this.message = raw.message || null;
    this.affected = raw.affected != null ? raw.affected : null;
    this.elapsed_ms = raw.elapsed_ms || null;
    this.columns = raw.columns || [];
    this.rows = (raw.rows || []).map(r => new Row(this.columns, r));
  }

  [Symbol.iterator]() { return this.rows[Symbol.iterator](); }
  get length() { return this.rows.length; }
}

class PulseDB {
  /**
   * @param {object} [opts]
   * @param {string} [opts.host='127.0.0.1']
   * @param {number} [opts.port=7878]
   */
  constructor(opts = {}) {
    this.host = opts.host || '127.0.0.1';
    this.port = opts.port || 7878;
    this._socket = null;
    this._buffer = '';
    this._pending = [];
  }

  /**
   * Connect and return the client.
   * @returns {Promise<PulseDB>}
   */
  static async connect(opts = {}) {
    const client = new PulseDB(opts);
    await client._connect();
    return client;
  }

  _connect() {
    return new Promise((resolve, reject) => {
      const sock = net.createConnection({ host: this.host, port: this.port }, () => {
        this._socket = sock;
        resolve(this);
      });

      sock.setEncoding('utf8');

      sock.on('data', (chunk) => {
        this._buffer += chunk;
        const lines = this._buffer.split('\n');
        this._buffer = lines.pop(); // keep incomplete tail
        for (const line of lines) {
          if (!line.trim()) continue;
          const raw = JSON.parse(line);
          const resolver = this._pending.shift();
          if (resolver) resolver(raw);
        }
      });

      sock.on('error', (err) => {
        this._pending.forEach(r => r({ error: err.message }));
        this._pending = [];
        reject(err);
      });

      sock.on('close', () => {
        this._pending.forEach(r => r({ error: 'connection closed' }));
        this._pending = [];
      });
    });
  }

  /**
   * Authenticate with the server.
   * @param {string} username
   * @param {string} password
   * @returns {Promise<PulseDB>}
   */
  async auth(username, password) {
    await this.query(`AUTH '${username}' '${password}'`);
    return this;
  }

  /**
   * Execute a PulseQL query.
   * @param {string} q
   * @returns {Promise<Result>}
   */
  query(q) {
    if (!this._socket) return Promise.reject(new PulseDBError('not connected'));

    return new Promise((resolve, reject) => {
      this._pending.push((raw) => {
        const result = new Result(raw);
        if (!result.ok) return reject(new PulseDBError(result.error));
        resolve(result);
      });
      this._socket.write(JSON.stringify({ query: q }) + '\n');
    });
  }

  /** Close the connection. */
  close() {
    if (this._socket) {
      this._socket.destroy();
      this._socket = null;
    }
  }

  /**
   * Run a callback with an auto-closing connection.
   * @param {function(PulseDB): Promise<any>} fn
   * @param {object} [opts]
   */
  static async withConnection(fn, opts = {}) {
    const db = await PulseDB.connect(opts);
    try {
      return await fn(db);
    } finally {
      db.close();
    }
  }
}

module.exports = { PulseDB, PulseDBError, Result, Row };
