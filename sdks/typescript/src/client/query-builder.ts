/**
 * Supabase-compatible fluent query builder for HeliosDB
 *
 * Provides a familiar PostgREST-style API for querying tables
 * without writing raw SQL.
 *
 * @example
 * ```typescript
 * const { data, error } = await db.from('users')
 *   .select('id, name, email')
 *   .eq('active', true)
 *   .order('created_at', { ascending: false })
 *   .limit(10);
 * ```
 */

export class QueryBuilder {
  private table: string;
  private baseUrl: string;
  private apiKey: string;
  private _select: string = '*';
  private _filters: string[] = [];
  private _order?: string;
  private _limit?: number;
  private _offset?: number;

  constructor(table: string, baseUrl: string, apiKey: string) {
    this.table = table;
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
  }

  /**
   * Select specific columns
   *
   * @param columns - Comma-separated column names or '*' for all
   */
  select(columns: string = '*'): this {
    this._select = columns;
    return this;
  }

  /**
   * Filter where column equals value
   */
  eq(column: string, value: unknown): this {
    this._filters.push(`${column}=eq.${value}`);
    return this;
  }

  /**
   * Filter where column does not equal value
   */
  neq(column: string, value: unknown): this {
    this._filters.push(`${column}=neq.${value}`);
    return this;
  }

  /**
   * Filter where column is greater than value
   */
  gt(column: string, value: unknown): this {
    this._filters.push(`${column}=gt.${value}`);
    return this;
  }

  /**
   * Filter where column is greater than or equal to value
   */
  gte(column: string, value: unknown): this {
    this._filters.push(`${column}=gte.${value}`);
    return this;
  }

  /**
   * Filter where column is less than value
   */
  lt(column: string, value: unknown): this {
    this._filters.push(`${column}=lt.${value}`);
    return this;
  }

  /**
   * Filter where column is less than or equal to value
   */
  lte(column: string, value: unknown): this {
    this._filters.push(`${column}=lte.${value}`);
    return this;
  }

  /**
   * Filter where column matches pattern (case-sensitive)
   */
  like(column: string, value: string): this {
    this._filters.push(`${column}=like.${value}`);
    return this;
  }

  /**
   * Filter where column matches pattern (case-insensitive)
   */
  ilike(column: string, value: string): this {
    this._filters.push(`${column}=ilike.${value}`);
    return this;
  }

  /**
   * Filter where column value is in the given list
   */
  in(column: string, values: unknown[]): this {
    this._filters.push(`${column}=in.(${values.join(',')})`);
    return this;
  }

  /**
   * Filter where column is null, true, or false
   */
  is(column: string, value: 'null' | 'true' | 'false'): this {
    this._filters.push(`${column}=is.${value}`);
    return this;
  }

  /**
   * Order results by column
   *
   * @param column - Column name to sort by
   * @param opts - Sort options; ascending defaults to true
   */
  order(column: string, opts?: { ascending?: boolean }): this {
    this._order = `${column}.${opts?.ascending === false ? 'desc' : 'asc'}`;
    return this;
  }

  /**
   * Limit the number of rows returned
   */
  limit(n: number): this {
    this._limit = n;
    return this;
  }

  /**
   * Skip the first n rows
   */
  offset(n: number): this {
    this._offset = n;
    return this;
  }

  /**
   * Execute the query and return results
   *
   * @returns Object with data array and error (null on success)
   */
  async execute(): Promise<{ data: unknown[]; error: string | null }> {
    const params = new URLSearchParams();
    params.set('select', this._select);
    for (const f of this._filters) {
      const eqIndex = f.indexOf('=');
      if (eqIndex === -1) continue;
      const key = f.substring(0, eqIndex);
      const val = f.substring(eqIndex + 1);
      params.append(key, val);
    }
    if (this._order) params.set('order', this._order);
    if (this._limit !== undefined) params.set('limit', String(this._limit));
    if (this._offset !== undefined) params.set('offset', String(this._offset));

    try {
      const resp = await fetch(`${this.baseUrl}/rest/v1/${this.table}?${params}`, {
        headers: { 'apikey': this.apiKey, 'Content-Type': 'application/json' }
      });
      if (!resp.ok) return { data: [], error: await resp.text() };
      const data = await resp.json() as unknown[];
      return { data, error: null };
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e);
      return { data: [], error: message };
    }
  }

  /**
   * Allow awaiting the builder directly.
   *
   * @example
   * ```typescript
   * const { data, error } = await db.from('users').select('*').eq('id', 1);
   * ```
   */
  then(
    resolve: (v: { data: unknown[]; error: string | null }) => void,
    reject?: (e: unknown) => void
  ) {
    return this.execute().then(resolve, reject);
  }

  /**
   * Execute and return exactly one row.
   * Returns an error if the query returns zero rows.
   */
  async single(): Promise<{ data: unknown | null; error: string | null }> {
    const result = await this.limit(1).execute();
    if (result.error) return { data: null, error: result.error };
    const first = result.data.length > 0 ? result.data[0] : null;
    if (first === null || first === undefined) {
      return { data: null, error: 'No rows returned' };
    }
    return { data: first, error: null };
  }

  /**
   * Execute and return at most one row.
   * Returns null data (without error) if no rows match.
   */
  async maybeSingle(): Promise<{ data: unknown | null; error: string | null }> {
    const result = await this.limit(1).execute();
    const first = result.data.length > 0 ? result.data[0] : null;
    return { data: first ?? null, error: null };
  }
}
