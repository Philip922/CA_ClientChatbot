import { FeedbackRating } from './feedback.model';
import { Source, dedupeSources } from './source.model';

export type MessageRole = 'user' | 'agent';

/**
 * Longest message a user can send. Mirrors `MAX_MESSAGE_CHARS` in the backend's
 * `app.py`, which rejects anything longer with a 422.
 */
export const MAX_MESSAGE_CHARS = 4000;

/** `streaming` while tokens arrive, `complete` once the stream closes, `error` if it fails. */
export type MessageStatus = 'streaming' | 'complete' | 'error';

/** The tools the agent can call. Unknown names from the backend are tolerated. */
export type ToolName = 'query_knowledge_base' | 'scrape_cadre_website' | 'escalate_to_human';

export interface ToolEvent {
    /** Backend-supplied call id when available, otherwise derived from the tool name. */
    id: string;
    tool: ToolName | string;
    status: 'running' | 'done';
    /** Populated from the matching `tool_end` event. */
    sources: Source[];
}

export interface Message {
    id: string;
    role: MessageRole;
    /** Accumulates token-by-token while streaming. */
    content: string;
    status: MessageStatus;
    timestamp: Date;
    toolEvents: ToolEvent[];
    feedback: FeedbackRating | null;
    /** Set when `status === 'error'`. */
    error?: string;
}

const TOOL_LABELS: Record<string, string> = {
    query_knowledge_base: 'Searching knowledge base…',
    scrape_cadre_website: 'Reading Cadre website…',
    escalate_to_human: 'Connecting you to the team…'
};

export function toolLabel(tool: string): string {
    return TOOL_LABELS[tool] ?? 'Working…';
}

/**
 * All sources for a message: the flattened union across completed tool events,
 * in the order the tools ran, deduplicated.
 */
export function messageSources(message: Message): Source[] {
    return dedupeSources(message.toolEvents.flatMap(event => event.sources));
}
