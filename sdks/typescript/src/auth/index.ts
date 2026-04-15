/**
 * HeliosDB authentication client
 *
 * Provides Supabase-compatible auth operations including
 * sign up, sign in, sign out, and user retrieval.
 *
 * @example
 * ```typescript
 * const { auth } = db;
 *
 * // Sign up a new user
 * const user = await auth.signUp({ email: 'user@example.com', password: 'secret' });
 *
 * // Sign in
 * const session = await auth.signIn({ email: 'user@example.com', password: 'secret' });
 *
 * // Get current user
 * const currentUser = await auth.getUser(session.access_token);
 * ```
 */

export interface AuthResponse {
  user?: {
    id: string;
    email: string;
    created_at: string;
  };
  access_token?: string;
  refresh_token?: string;
  error?: string;
}

export class AuthClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(baseUrl: string, apiKey: string) {
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
  }

  /**
   * Register a new user
   *
   * @param opts - Email and password for the new account
   * @returns The created user and session tokens
   */
  async signUp(opts: { email: string; password: string }): Promise<AuthResponse> {
    const resp = await fetch(`${this.baseUrl}/auth/v1/signup`, {
      method: 'POST',
      headers: { 'apikey': this.apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify(opts)
    });
    return resp.json() as Promise<AuthResponse>;
  }

  /**
   * Sign in with email and password
   *
   * @param opts - Email and password credentials
   * @returns Session tokens on success
   */
  async signIn(opts: { email: string; password: string }): Promise<AuthResponse> {
    const resp = await fetch(`${this.baseUrl}/auth/v1/token`, {
      method: 'POST',
      headers: { 'apikey': this.apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify(opts)
    });
    return resp.json() as Promise<AuthResponse>;
  }

  /**
   * Sign out the current user
   */
  async signOut(): Promise<void> {
    await fetch(`${this.baseUrl}/auth/v1/logout`, {
      method: 'POST',
      headers: { 'apikey': this.apiKey }
    });
  }

  /**
   * Retrieve the current user from a JWT token
   *
   * @param token - Bearer token from sign in
   * @returns The authenticated user
   */
  async getUser(token: string): Promise<AuthResponse> {
    const resp = await fetch(`${this.baseUrl}/auth/v1/user`, {
      headers: { 'apikey': this.apiKey, 'Authorization': `Bearer ${token}` }
    });
    return resp.json() as Promise<AuthResponse>;
  }
}
