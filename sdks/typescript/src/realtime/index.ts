/**
 * HeliosDB realtime subscriptions
 *
 * Provides Supabase-compatible realtime change notifications
 * over WebSocket connections.
 *
 * @example
 * ```typescript
 * const channel = db.channel('users');
 *
 * channel
 *   .on('INSERT', (payload) => console.log('New user:', payload))
 *   .on('UPDATE', (payload) => console.log('Updated:', payload))
 *   .on('DELETE', (payload) => console.log('Deleted:', payload))
 *   .subscribe();
 *
 * // Later, unsubscribe
 * channel.unsubscribe();
 * ```
 */

export type RealtimeEvent = 'INSERT' | 'UPDATE' | 'DELETE' | '*';

export interface RealtimePayload {
  type: string;
  table: string;
  schema: string;
  old_record?: Record<string, unknown>;
  new_record?: Record<string, unknown>;
  commit_timestamp?: string;
}

export class RealtimeChannel {
  private ws: WebSocket | null = null;
  private callbacks: Map<string, ((payload: RealtimePayload) => void)[]> = new Map();
  private url: string;
  private topic: string;

  constructor(url: string, topic: string) {
    this.url = url;
    this.topic = topic;
  }

  /**
   * Register a callback for a specific change event type
   *
   * @param event - The event type to listen for, or '*' for all events
   * @param callback - Function called with the change payload
   */
  on(event: RealtimeEvent, callback: (payload: RealtimePayload) => void): this {
    const key = event === '*' ? 'all' : event;
    const existing = this.callbacks.get(key);
    if (existing) {
      existing.push(callback);
    } else {
      this.callbacks.set(key, [callback]);
    }
    return this;
  }

  /**
   * Open the WebSocket connection and start receiving events
   */
  subscribe(): this {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.ws?.send(JSON.stringify({
        event: 'phx_join',
        topic: `realtime:public:${this.topic}`,
        payload: {
          config: {
            postgres_changes: [{
              event: '*',
              schema: 'public',
              table: this.topic
            }]
          }
        },
        ref: '1'
      }));
    };
    this.ws.onmessage = (msg) => {
      let data: { event?: string; payload?: RealtimePayload };
      try {
        data = JSON.parse(msg.data as string) as { event?: string; payload?: RealtimePayload };
      } catch {
        return;
      }
      if (data.event === 'postgres_changes' && data.payload) {
        const eventType = data.payload.type;
        const typeCallbacks = this.callbacks.get(eventType);
        if (typeCallbacks) {
          for (const cb of typeCallbacks) {
            cb(data.payload);
          }
        }
        const allCallbacks = this.callbacks.get('all');
        if (allCallbacks) {
          for (const cb of allCallbacks) {
            cb(data.payload);
          }
        }
      }
    };
    return this;
  }

  /**
   * Close the WebSocket connection and stop receiving events
   */
  unsubscribe(): void {
    this.ws?.close();
    this.ws = null;
  }
}
