export interface SseEvent {
    /** The `event:` field, or `message` when the record omits one. */
    type: string;
    /** Joined `data:` lines, newline-separated as the SSE spec requires. */
    data: string;
}

/**
 * Incremental SSE decoder.
 *
 * Network chunks split records anywhere — mid-field, mid-UTF-8 — so the tail of
 * the buffer is held back until a blank line proves a record is complete.
 */
export class SseDecoder {
    private buffer = '';

    push(chunk: string): SseEvent[] {
        this.buffer += chunk.replace(/\r\n?/g, '\n');
        const events: SseEvent[] = [];

        let separator = this.buffer.indexOf('\n\n');
        while (separator !== -1) {
            const record = this.buffer.slice(0, separator);
            this.buffer = this.buffer.slice(separator + 2);
            const event = parseRecord(record);
            if (event) events.push(event);
            separator = this.buffer.indexOf('\n\n');
        }
        return events;
    }

    /** Flushes a trailing record that arrived without its closing blank line. */
    flush(): SseEvent[] {
        const record = this.buffer;
        this.buffer = '';
        const event = parseRecord(record);
        return event ? [event] : [];
    }
}

function parseRecord(record: string): SseEvent | null {
    let type = 'message';
    const data: string[] = [];

    for (const line of record.split('\n')) {
        if (!line || line.startsWith(':')) continue;
        const colon = line.indexOf(':');
        const field = colon === -1 ? line : line.slice(0, colon);
        // A single space after the colon is part of the framing, not the value.
        let value = colon === -1 ? '' : line.slice(colon + 1);
        if (value.startsWith(' ')) value = value.slice(1);

        if (field === 'event') type = value;
        else if (field === 'data') data.push(value);
    }

    if (!data.length) return null;
    return { type, data: data.join('\n') };
}

/**
 * Parses an event payload. Falls back to the raw string for backends that send
 * bare tokens rather than JSON objects.
 */
export function parsePayload(data: string): unknown {
    try {
        return JSON.parse(data);
    } catch {
        return data;
    }
}
