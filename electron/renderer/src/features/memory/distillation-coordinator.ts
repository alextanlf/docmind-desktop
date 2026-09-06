export class DistillationCoordinator {
  private readonly tails = new Map<string, Promise<unknown>>();

  get pendingCount() {
    return this.tails.size;
  }

  enqueue<Value>(id: string, operation: () => Promise<Value>): Promise<Value> {
    const previous = this.tails.get(id) ?? Promise.resolve();
    const result = previous.catch(() => undefined).then(operation);
    this.tails.set(id, result);
    const cleanup = () => {
      if (this.tails.get(id) === result) this.tails.delete(id);
    };
    void result.then(cleanup, cleanup);
    return result;
  }
}

export const distillationCoordinator = new DistillationCoordinator();
