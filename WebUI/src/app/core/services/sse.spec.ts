import { SseDecoder, parsePayload } from './sse';

describe('SseDecoder', () => {
    let decoder: SseDecoder;

    beforeEach(() => {
        decoder = new SseDecoder();
    });

    it('decodes a complete record', () => {
        expect(decoder.push('event: token\ndata: {"content":"Hi"}\n\n')).toEqual([
            { type: 'token', data: '{"content":"Hi"}' }
        ]);
    });

    it('holds a record split across chunks until its blank line arrives', () => {
        expect(decoder.push('event: tok')).toEqual([]);
        expect(decoder.push('en\ndata: {"conte')).toEqual([]);
        expect(decoder.push('nt":"Hi"}\n')).toEqual([]);
        expect(decoder.push('\n')).toEqual([{ type: 'token', data: '{"content":"Hi"}' }]);
    });

    it('decodes several records from one chunk', () => {
        const events = decoder.push('event: a\ndata: 1\n\nevent: b\ndata: 2\n\n');
        expect(events).toEqual([
            { type: 'a', data: '1' },
            { type: 'b', data: '2' }
        ]);
    });

    it('accepts CRLF and CR line endings', () => {
        expect(decoder.push('event: a\r\ndata: 1\r\n\r\nevent: b\rdata: 2\r\r')).toEqual([
            { type: 'a', data: '1' },
            { type: 'b', data: '2' }
        ]);
    });

    it('joins multiple data lines with newlines', () => {
        expect(decoder.push('data: one\ndata: two\n\n')).toEqual([{ type: 'message', data: 'one\ntwo' }]);
    });

    it('defaults the type to "message" and strips only one leading space', () => {
        expect(decoder.push('data:  padded\n\n')).toEqual([{ type: 'message', data: ' padded' }]);
    });

    it('ignores comments and records without data', () => {
        expect(decoder.push(': keep-alive\n\nevent: ping\n\n')).toEqual([]);
    });

    it('flushes a trailing record that never got its blank line', () => {
        decoder.push('data: [DONE]');
        expect(decoder.flush()).toEqual([{ type: 'message', data: '[DONE]' }]);
        expect(decoder.flush()).toEqual([]);
    });
});

describe('parsePayload', () => {
    it('parses JSON', () => {
        expect(parsePayload('{"content":"x"}')).toEqual({ content: 'x' });
    });

    it('falls back to the raw string for non-JSON data', () => {
        expect(parsePayload('plain token')).toBe('plain token');
    });
});
