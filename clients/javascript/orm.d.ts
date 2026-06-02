/**
 * PulseDB JavaScript ORM — TypeScript definitions.
 */

import { PulseDB } from './index';

// ── Data types ────────────────────────────────────────────────────────────────

export type DataType = 'int' | 'float' | 'text' | 'bool' | 'json' | 'blob' | 'vector';

export declare const DataTypes: {
  readonly INT:    'int';
  readonly FLOAT:  'float';
  readonly TEXT:   'text';
  readonly BOOL:   'bool';
  readonly JSON:   'json';
  readonly BLOB:   'blob';
  readonly VECTOR: 'vector';
};

// ── Schema definition ─────────────────────────────────────────────────────────

export interface FieldDef {
  type:          DataType;
  primaryKey?:   boolean;
  defaultValue?: unknown;
  nullable?:     boolean;
}

export type Schema = Record<string, FieldDef>;

// ── WHERE conditions ──────────────────────────────────────────────────────────

export type ComparisonOp<T> = T | {
  gt?:  T;
  gte?: T;
  lt?:  T;
  lte?: T;
  ne?:  T;
  in?:  T[];
};

export type WhereClause<T extends Record<string, unknown>> = {
  [K in keyof T]?: ComparisonOp<T[K]>;
};

// ── QuerySet ──────────────────────────────────────────────────────────────────

export declare class QuerySet<TInstance extends Model<any>> {
  where(conditions: Record<string, unknown>): QuerySet<TInstance>;
  orderBy(...columns: string[]): QuerySet<TInstance>;
  limit(n: number): QuerySet<TInstance>;

  all(): Promise<TInstance[]>;
  first(): Promise<TInstance | null>;
  count(): Promise<number>;

  update(values: Partial<Record<string, unknown>>): Promise<void>;
  delete(): Promise<void>;

  [Symbol.asyncIterator](): AsyncIterator<TInstance>;
}

// ── Model instance ────────────────────────────────────────────────────────────

export declare class Model<TData extends Record<string, unknown>> {
  constructor(data?: Partial<TData>);

  save(): Promise<void>;
  delete(): Promise<void>;
  update(values: Partial<TData>): Promise<void>;
  toJSON(): TData;
  toString(): string;
}

// ── Model static interface ────────────────────────────────────────────────────

export interface ModelStatic<TData extends Record<string, unknown>> {
  table:   string;
  db:      PulseDB;
  _schema: Schema;

  new(data?: Partial<TData>): Model<TData> & TData;

  // Schema
  createTable(): Promise<void>;
  dropTable(): Promise<void>;
  createIndex(...columns: string[]): Promise<void>;

  // Write
  create(data: TData): Promise<Model<TData> & TData>;
  bulkCreate(records: TData[], opts?: { transaction?: boolean }): Promise<Array<Model<TData> & TData>>;

  // Read
  findAll(opts?: {
    where?:   WhereClause<TData>;
    orderBy?: string | string[];
    limit?:   number;
  }): Promise<Array<Model<TData> & TData>>;

  findOne(opts?: {
    where?:   WhereClause<TData>;
    orderBy?: string | string[];
  }): Promise<(Model<TData> & TData) | null>;

  findByPk(pk: unknown): Promise<(Model<TData> & TData) | null>;
  count(opts?: { where?: WhereClause<TData> }): Promise<number>;

  // Update / Delete
  update(values: Partial<TData>, opts?: { where?: WhereClause<TData> }): Promise<void>;
  destroy(opts?: { where?: WhereClause<TData> }): Promise<void>;

  // Search
  similar(column: string, vector: number[], opts?: { limit?: number }): Promise<Array<Model<TData> & TData>>;
  fuzzy(column: string, pattern: string, opts?: { limit?: number }): Promise<Array<Model<TData> & TData>>;

  // QuerySet
  where(conditions: WhereClause<TData>): QuerySet<Model<TData> & TData>;
  orderBy(...columns: string[]): QuerySet<Model<TData> & TData>;
  limit(n: number): QuerySet<Model<TData> & TData>;
}

// ── defineModel ───────────────────────────────────────────────────────────────

export declare function defineModel<TData extends Record<string, unknown>>(
  tableName: string,
  schema:    Schema,
  options:   { db: PulseDB }
): ModelStatic<TData>;

// ── withTransaction ───────────────────────────────────────────────────────────

export declare function withTransaction<T>(
  db: PulseDB,
  fn: () => Promise<T>
): Promise<T>;
