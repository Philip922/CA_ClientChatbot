import { Injectable, computed, signal } from '@angular/core';
import { environment } from '../../../environments/environment';
import {
    Message,
    MessageRole,
    ToolEvent
} from '../../features/chat/models/message.model';
import { FeedbackRating } from '../../features/chat/models/feedback.model';
import { Source } from '../../features/chat/models/source.model';
import { SseDecoder, parsePayload } from './sse';

/**
 * Longest silence tolerated from the backend before the stream is abandoned.
 * Reset on every chunk, and tool calls emit `tool_start`/`tool_end`, so this
 * only trips on a genuinely stalled connection, not a slow answer.
 */
const IDLE_TIMEOUT_MS = 60_000;

/**
 * An error whose message is written for the user and is shown as-is. Anything
 * else that reaches `fail()` is mapped to friendly text by `describeFailure()`.
 */
class ChatError extends Error {
    constructor(message: string) {
        super(message);
        this.name = 'ChatError';
    }
}

/** Abort reason for a stalled stream, so it can be told apart from a user abort. */
class StreamTimeoutError extends ChatError {
    constructor() {
        super('The agent took too long to respond. Please try again.');
        this.name = 'StreamTimeoutError';
    }
}

/** A non-2xx response from `/chat`; the status picks the message shown. */
class HttpStatusError extends Error {
    constructor(readonly status: number) {
        super(`Chat request failed with HTTP ${status}`);
        this.name = 'HttpStatusError';
    }
}

/** Shape of one turn as the backend expects it in the request body. */
interface HistoryTurn {
    role: 'user' | 'assistant';
    content: string;
}

/**
 * Owns the conversation.
 *
 * Streams over `fetch` + `ReadableStream` rather than `EventSource` because the
 * message and history have to go up in a POST body, which `EventSource` cannot do.
 */
@Injectable({ providedIn: 'root' })
export class ChatService {
    private readonly _messages = signal<Message[]>([]);
    readonly messages = this._messages.asReadonly();

    readonly isStreaming = computed(() => this._messages().some(m => m.status === 'streaming'));
    readonly isEmpty = computed(() => this._messages().length === 0);

    private readonly endpoint = `${environment.apiUrl}/chat`;
    private controller: AbortController | null = null;

    /**
     * Tokens received since the last frame. Writing each token straight into the
     * signal would re-parse the whole answer's markdown and replace its DOM once
     * per token; batching caps that at once per frame however fast tokens arrive.
     */
    private pendingTokens: { agentId: string; text: string } | null = null;
    private flushFrame: number | null = null;

    async sendMessage(text: string): Promise<void> {
        const trimmed = text.trim();
        if (!trimmed || this.isStreaming()) return;

        // History must be captured before the new turn is appended.
        const history = this.getHistory();

        this._messages.update(messages => [
            ...messages,
            createMessage('user', trimmed, 'complete'),
            createMessage('agent', '', 'streaming')
        ]);

        const agentId = this._messages()[this._messages().length - 1].id;
        await this.stream(agentId, trimmed, history);
    }

    /**
     * Drops a failed agent turn and re-sends the user message that preceded it.
     * Backs the inline retry shown on the error bubble.
     */
    async retry(): Promise<void> {
        const messages = this._messages();
        const last = messages[messages.length - 1];
        if (!last || last.role !== 'agent' || last.status !== 'error') return;

        const prompt = messages[messages.length - 2];
        if (!prompt || prompt.role !== 'user') return;

        this._messages.set(messages.slice(0, -2));
        await this.sendMessage(prompt.content);
    }

    /** Conversation so far, formatted for the next request body. */
    getHistory(): HistoryTurn[] {
        return this._messages()
            .filter(m => m.status === 'complete' && m.content.trim().length > 0)
            .map(m => ({
                role: m.role === 'agent' ? ('assistant' as const) : ('user' as const),
                content: m.content
            }));
    }

    setFeedback(messageId: string, rating: FeedbackRating | null): void {
        this.patch(messageId, message => ({ ...message, feedback: rating }));
    }

    /**
     * Cancels the reply in flight. Whatever text already arrived is kept as a
     * complete answer; a reply stopped before its first token becomes a
     * retryable error instead.
     */
    stop(): void {
        // Buffered tokens count as received text when deciding what to keep.
        this.flushTokens();
        const streaming = this._messages().find(m => m.status === 'streaming');
        this.controller?.abort();
        this.controller = null;
        if (!streaming) return;

        if (streaming.content.trim()) {
            this.finish(streaming.id);
        } else {
            this.markError(streaming.id, 'Response stopped.');
        }
    }

    clear(): void {
        this.controller?.abort();
        this.controller = null;
        this.discardTokens();
        this._messages.set([]);
    }

    private async stream(agentId: string, message: string, history: HistoryTurn[]): Promise<void> {
        this.controller?.abort();
        const controller = new AbortController();
        this.controller = controller;

        // Armed before the fetch so a request that never gets response headers
        // is caught too, then re-armed on every chunk read.
        let idleTimer: ReturnType<typeof setTimeout> | undefined;
        const armWatchdog = () => {
            clearTimeout(idleTimer);
            idleTimer = setTimeout(() => controller.abort(new StreamTimeoutError()), IDLE_TIMEOUT_MS);
        };
        armWatchdog();

        try {
            const response = await fetch(this.endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Accept: 'text/event-stream'
                },
                body: JSON.stringify({ message, history }),
                signal: controller.signal
            });

            if (!response.ok) throw new HttpStatusError(response.status);
            if (!response.body) throw new Error('Response carried no body');

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            const sse = new SseDecoder();

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                armWatchdog();
                for (const event of sse.push(decoder.decode(value, { stream: true }))) {
                    if (this.handleEvent(agentId, event.type, event.data)) {
                        // The reply is settled; don't wait on the server to close the socket.
                        reader.cancel().catch(() => {});
                        return;
                    }
                }
            }
            for (const event of sse.flush()) {
                if (this.handleEvent(agentId, event.type, event.data)) return;
            }

            // The backend always ends with `[DONE]`, so a stream that closes without
            // one was cut off (Lambda timeout, dropped connection). Showing the partial
            // text as a finished answer would hide that from the user.
            throw new ChatError('The connection dropped before the answer finished. Please try again.');
        } catch (error) {
            const reason: unknown = controller.signal.reason;
            if (reason instanceof StreamTimeoutError) {
                this.fail(agentId, reason);
                return;
            }
            // Aborted by stop(), clear() or a newer request, which own the UI state.
            if (controller.signal.aborted) return;
            this.fail(agentId, error);
        } finally {
            clearTimeout(idleTimer);
            if (this.controller === controller) this.controller = null;
        }
    }

    /** Applies one SSE event. Returns true when the event ends the stream. */
    private handleEvent(agentId: string, type: string, data: string): boolean {
        if (data === '[DONE]') {
            this.finish(agentId);
            return true;
        }
        const payload = parsePayload(data);

        switch (type) {
            case 'token':
                this.appendToken(agentId, readToken(payload));
                break;
            case 'tool_start':
                this.startTool(agentId, payload);
                break;
            case 'tool_end':
                this.endTool(agentId, payload);
                break;
            case 'sources':
                this.attachTrailingSources(agentId, readSources(payload));
                break;
            case 'error':
                // The backend writes this message for the user, so it is shown as-is.
                this.fail(
                    agentId,
                    new ChatError(readMessage(payload) ?? 'The agent hit an error. Please try again.')
                );
                return true;
            case 'done':
                this.finish(agentId);
                return true;
            default:
                // An unrecognised event type is ignored so a backend addition
                // never breaks an already-deployed frontend.
                break;
        }
        return false;
    }

    private appendToken(agentId: string, token: string): void {
        if (!token) return;
        if (this.pendingTokens && this.pendingTokens.agentId !== agentId) this.flushTokens();

        if (this.pendingTokens) {
            this.pendingTokens.text += token;
        } else {
            this.pendingTokens = { agentId, text: token };
        }
        this.flushFrame ??= requestAnimationFrame(() => this.flushTokens());
    }

    /** Writes buffered tokens into their message. Safe to call when nothing is buffered. */
    private flushTokens(): void {
        const pending = this.pendingTokens;
        this.discardTokens();
        if (!pending) return;
        this.patch(pending.agentId, message => ({ ...message, content: message.content + pending.text }));
    }

    private discardTokens(): void {
        if (this.flushFrame !== null) cancelAnimationFrame(this.flushFrame);
        this.flushFrame = null;
        this.pendingTokens = null;
    }

    private startTool(agentId: string, payload: unknown): void {
        const tool = readTool(payload);
        if (!tool) return;
        const id = readId(payload) ?? `${tool}-${Date.now()}`;

        this.patch(agentId, message => ({
            ...message,
            toolEvents: [...message.toolEvents, { id, tool, status: 'running', sources: [] }]
        }));
    }

    private endTool(agentId: string, payload: unknown): void {
        const tool = readTool(payload);
        const id = readId(payload);
        const sources = readSources(payload);

        this.patch(agentId, message => {
            // Match on the call id when the backend sends one, otherwise close
            // the most recent still-running event for that tool.
            const index = findOpenTool(message.toolEvents, id, tool);
            if (index === -1) {
                // A `tool_end` with no matching start still deserves its sources.
                if (!sources.length) return message;
                return {
                    ...message,
                    toolEvents: [
                        ...message.toolEvents,
                        { id: id ?? `${tool ?? 'tool'}-${Date.now()}`, tool: tool ?? 'unknown', status: 'done', sources }
                    ]
                };
            }

            const toolEvents = [...message.toolEvents];
            toolEvents[index] = { ...toolEvents[index], status: 'done', sources };
            return { ...message, toolEvents };
        });
    }

    /**
     * The optional end-of-stream `sources` event carries the full deduplicated
     * list for the message. It is parked on a synthetic done event so the
     * sources panel picks it up through the same path as tool sources.
     */
    private attachTrailingSources(agentId: string, sources: Source[]): void {
        if (!sources.length) return;
        this.patch(agentId, message => ({
            ...message,
            toolEvents: [
                ...message.toolEvents,
                { id: 'stream-sources', tool: 'summary', status: 'done', sources }
            ]
        }));
    }

    private finish(agentId: string): void {
        // The empty-response check below must see every token received.
        this.flushTokens();
        this.patch(agentId, message => {
            if (message.status !== 'streaming') return message;
            return {
                ...message,
                status: message.content.trim() ? 'complete' : 'error',
                error: message.content.trim() ? undefined : 'The agent returned an empty response.',
                toolEvents: message.toolEvents.map(event =>
                    event.status === 'running' ? { ...event, status: 'done' as const } : event
                )
            };
        });
    }

    /** Logs the raw error for debugging and shows the user a friendly version. */
    private fail(agentId: string, error: unknown): void {
        console.error('[chat] reply failed:', error);
        this.markError(agentId, describeFailure(error));
    }

    private markError(agentId: string, text: string): void {
        this.flushTokens();
        // A message that already finished (e.g. `[DONE]` arrived but the socket
        // lingered until the watchdog fired) keeps its answer.
        this.patch(agentId, message =>
            message.status === 'streaming' ? { ...message, status: 'error', error: text } : message
        );
    }

    private patch(id: string, update: (message: Message) => Message): void {
        this._messages.update(messages =>
            messages.map(message => (message.id === id ? update(message) : message))
        );
    }
}

/**
 * Turns any failure into text fit for the error bubble. Raw messages such as
 * `Failed to fetch` or an HTTP status never reach the user; they go to the
 * console via `fail()` instead.
 */
function describeFailure(error: unknown): string {
    if (error instanceof ChatError) return error.message;

    if (error instanceof HttpStatusError) {
        if (error.status === 429) return 'Too many requests right now. Please wait a moment and try again.';
        if (error.status === 413) return 'That message is too long. Please shorten it and try again.';
        if (error.status >= 500) return 'The service is having trouble right now. Please try again shortly.';
        return 'That message could not be processed. Please try again.';
    }

    if (!navigator.onLine) return 'You appear to be offline. Check your connection and try again.';

    // fetch rejects with a TypeError for network failures: DNS, CORS, refused connection.
    if (error instanceof TypeError) {
        return 'Could not reach the server. Check your connection and try again.';
    }

    return 'Something went wrong. Please try again.';
}

function createMessage(role: MessageRole, content: string, status: Message['status']): Message {
    return {
        id: crypto.randomUUID(),
        role,
        content,
        status,
        timestamp: new Date(),
        toolEvents: [],
        feedback: null
    };
}

function findOpenTool(events: ToolEvent[], id: string | null, tool: string | null): number {
    if (id) {
        const byId = events.findIndex(event => event.id === id);
        if (byId !== -1) return byId;
    }
    for (let i = events.length - 1; i >= 0; i--) {
        if (events[i].status === 'running' && (!tool || events[i].tool === tool)) return i;
    }
    return -1;
}

function asRecord(payload: unknown): Record<string, unknown> | null {
    return payload !== null && typeof payload === 'object' ? (payload as Record<string, unknown>) : null;
}

/** Accepts `{content}`, `{token}`, `{text}`, or a bare string token. */
function readToken(payload: unknown): string {
    if (typeof payload === 'string') return payload;
    if (typeof payload === 'number') return String(payload);
    const record = asRecord(payload);
    if (!record) return '';
    for (const key of ['content', 'token', 'text', 'delta']) {
        const value = record[key];
        if (typeof value === 'string') return value;
    }
    return '';
}

function readTool(payload: unknown): string | null {
    if (typeof payload === 'string') return payload;
    const record = asRecord(payload);
    if (!record) return null;
    for (const key of ['tool', 'name', 'tool_name']) {
        const value = record[key];
        if (typeof value === 'string') return value;
    }
    return null;
}

function readId(payload: unknown): string | null {
    const record = asRecord(payload);
    if (!record) return null;
    for (const key of ['id', 'tool_call_id', 'call_id']) {
        const value = record[key];
        if (typeof value === 'string') return value;
    }
    return null;
}

function readMessage(payload: unknown): string | null {
    if (typeof payload === 'string') return payload;
    const record = asRecord(payload);
    if (!record) return null;
    for (const key of ['message', 'detail', 'error']) {
        const value = record[key];
        if (typeof value === 'string') return value;
    }
    return null;
}

function readSources(payload: unknown): Source[] {
    const raw = Array.isArray(payload) ? payload : asRecord(payload)?.['sources'];
    if (!Array.isArray(raw)) return [];

    return raw.flatMap((entry): Source[] => {
        const record = asRecord(entry);
        if (!record) return [];

        const url = typeof record['url'] === 'string' ? record['url'] : undefined;
        const excerpt =
            typeof record['excerpt'] === 'string'
                ? record['excerpt']
                : typeof record['content'] === 'string'
                  ? record['content']
                  : typeof record['text'] === 'string'
                    ? record['text']
                    : undefined;

        // Trust an explicit `type` when the backend sends one; otherwise the
        // presence of a URL is what distinguishes a page from a KB chunk.
        const declared = record['type'];
        const type = declared === 'url' || declared === 'document' ? declared : url ? 'url' : 'document';

        const label =
            typeof record['label'] === 'string'
                ? record['label']
                : typeof record['title'] === 'string'
                  ? record['title']
                  : typeof record['name'] === 'string'
                    ? record['name']
                    : type === 'url'
                      ? (url ?? 'Source')
                      : 'Knowledge base';

        if (type === 'url' && !url) return [];
        if (type === 'document' && !excerpt) return [{ type, label }];
        return [{ type, label, url, excerpt }];
    });
}
