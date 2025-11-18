import type { SDKUserMessage } from "@anthropic-ai/claude-agent-sdk";

/**
 * A message queue that supports async iteration for streaming messages to Claude SDK.
 * Allows pushing messages dynamically while the SDK is consuming them.
 */
export class MessageQueue implements AsyncIterable<SDKUserMessage> {
  private queue: SDKUserMessage[] = [];
  private resolvers: Array<(value: IteratorResult<SDKUserMessage>) => void> = [];
  private closed = false;
  private error: Error | null = null;

  /**
   * Push a new message to the queue.
   * If there's a pending iterator waiting, it will be resolved immediately.
   */
  push(message: SDKUserMessage): void {
    if (this.closed) {
      throw new Error("Cannot push to a closed MessageQueue");
    }

    if (this.resolvers.length > 0) {
      // There's a waiting iterator, resolve it immediately
      const resolve = this.resolvers.shift()!;
      resolve({ value: message, done: false });
    } else {
      // No waiting iterator, add to queue
      this.queue.push(message);
    }
  }

  /**
   * Close the queue. No more messages can be pushed.
   * All pending and future iterations will complete.
   */
  close(): void {
    this.closed = true;
    // Resolve all pending resolvers with done=true
    while (this.resolvers.length > 0) {
      const resolve = this.resolvers.shift()!;
      resolve({ value: undefined as any, done: true });
    }
  }

  /**
   * Close the queue with an error.
   * All pending and future iterations will throw this error.
   */
  closeWithError(error: Error): void {
    this.error = error;
    this.closed = true;
    // Note: We don't reject pending promises here because AsyncIterator
    // doesn't have a standard way to propagate errors during iteration.
    // Instead, we'll throw the error on the next iteration.
    while (this.resolvers.length > 0) {
      const resolve = this.resolvers.shift()!;
      resolve({ value: undefined as any, done: true });
    }
  }

  /**
   * Check if the queue is closed
   */
  isClosed(): boolean {
    return this.closed;
  }

  /**
   * Get the number of messages currently in the queue
   */
  get length(): number {
    return this.queue.length;
  }

  async *[Symbol.asyncIterator](): AsyncIterator<SDKUserMessage> {
    while (true) {
      // Check for errors first
      if (this.error) {
        throw this.error;
      }

      // If we have messages in the queue, yield them
      if (this.queue.length > 0) {
        yield this.queue.shift()!;
        continue;
      }

      // If the queue is closed and empty, we're done
      if (this.closed) {
        return;
      }

      // Wait for a new message or close
      const result = await new Promise<IteratorResult<SDKUserMessage>>((resolve) => {
        this.resolvers.push(resolve);
      });

      if (result.done) {
        return;
      }

      yield result.value;
    }
  }
}
