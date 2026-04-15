/**
 * Conversation memory backed by HeliosDB.
 * Compatible with LangChain.js memory interface.
 *
 * @example
 * ```typescript
 * import { HeliosDB } from '@heliosdb/client';
 * import { HeliosDBMemory } from '@heliosdb/client/memory';
 *
 * const db = new HeliosDB({ url: 'http://localhost:8080' });
 * const memory = new HeliosDBMemory(db, 'session-abc');
 *
 * // Save a conversation turn
 * await memory.saveContext(
 *   { input: 'What is HeliosDB?' },
 *   { output: 'An AI-native embedded database.' }
 * );
 *
 * // Load the full history
 * const { history } = await memory.loadMemoryVariables();
 * console.log(history);
 *
 * // Clear the session
 * await memory.clear();
 * ```
 *
 * @packageDocumentation
 */

import type { HeliosDB } from '../client/heliosdb';

/**
 * Conversation memory backed by HeliosDB.
 *
 * Persists input/output pairs in HeliosDB's agent memory store so that
 * conversation history survives process restarts and can be shared across
 * services.  Implements the same `saveContext` / `loadMemoryVariables` /
 * `clear` contract used by LangChain.js `BaseMemory`, making it a drop-in
 * replacement for `BufferMemory` or `ConversationSummaryMemory`.
 */
export class HeliosDBMemory {
  private client: HeliosDB;
  private sessionId: string;

  /**
   * Create a new memory instance tied to a specific session.
   *
   * @param client - A connected HeliosDB client instance.
   * @param sessionId - Unique identifier for this conversation session.
   *   Different session IDs keep histories isolated from one another.
   */
  constructor(client: HeliosDB, sessionId: string) {
    this.client = client;
    this.sessionId = sessionId;
  }

  /**
   * Persist an input/output pair from a chain run.
   *
   * Both objects are JSON-serialised and stored as a single memory entry
   * so that the full context of each exchange is preserved.
   *
   * @param input - The chain input (typically `{ input: "user message" }`).
   * @param output - The chain output (typically `{ output: "assistant reply" }`).
   */
  async saveContext(
    input: Record<string, any>,
    output: Record<string, any>
  ): Promise<void> {
    const memory = this.client.agentMemory(this.sessionId);
    await memory.saveContext(input, output);
  }

  /**
   * Load the conversation history as a single string.
   *
   * Returns an object with a `history` key containing newline-delimited
   * messages, matching the LangChain.js `BaseMemory` return shape.
   *
   * @returns An object `{ history: string }` with the formatted history.
   */
  async loadMemoryVariables(): Promise<{ history: string }> {
    const memory = this.client.agentMemory(this.sessionId);
    return memory.loadMemoryVariables();
  }

  /**
   * Delete all messages for this session from HeliosDB.
   */
  async clear(): Promise<void> {
    const memory = this.client.agentMemory(this.sessionId);
    await memory.clear();
  }
}
