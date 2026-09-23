import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { messageSources } from '../../features/chat/models/message.model';
import { ChatService } from './chat.service';

const encoder = new TextEncoder();

function event(type: string, payload: unknown): string {
    return `event: ${type}\ndata: ${JSON.stringify(payload)}\n\n`;
}

const DONE = 'data: [DONE]\n\n';

/** A 200 SSE response that emits `chunks` and then closes. */
function sseResponse(...chunks: string[]): Response {
    const body = new ReadableStream<Uint8Array>({
        start(controller) {
            for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
            controller.close();
        }
    });
    return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

/** A complete reply whose text is `answer`. */
function answer(text: string): Response {
    return sseResponse(event('token', { content: text }), DONE);
}

/** A fetch that stays pending until its signal aborts, then rejects like the real one. */
function hangingFetch(_url: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    return new Promise((_, reject) => {
        init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason));
    });
}

const settle = () => new Promise(resolve => setTimeout(resolve, 0));

describe('ChatService', () => {
    let service: ChatService;
    let fetchSpy: jasmine.Spy<typeof fetch>;

    beforeEach(() => {
        TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
        service = TestBed.inject(ChatService);
        fetchSpy = spyOn(window, 'fetch');
        // fail() logs the raw error for debugging; keep test output clean.
        spyOn(console, 'error');
    });

    afterEach(() => service.clear());

    const lastMessage = () => service.messages()[service.messages().length - 1];

    /** The `history` the most recent request sent. */
    function sentHistory(): { role: string; content: string }[] {
        const init = fetchSpy.calls.mostRecent().args[1]!;
        return JSON.parse(init.body as string).history;
    }

    /** Sends each question and completes it with a matching answer. */
    async function converse(...questions: string[]): Promise<void> {
        for (const question of questions) {
            fetchSpy.and.returnValue(Promise.resolve(answer(`answer to ${question}`)));
            await service.sendMessage(question);
        }
    }

    describe('sendMessage', () => {
        it('posts the message and streams tokens into a completed agent message', async () => {
            fetchSpy.and.returnValue(
                Promise.resolve(
                    sseResponse(event('token', { content: 'Hel' }), event('token', { content: 'lo' }), DONE)
                )
            );

            await service.sendMessage('  Hi there  ');

            const [url, init] = fetchSpy.calls.mostRecent().args;
            expect(url).toBe('/chat');
            expect(init!.method).toBe('POST');
            expect(JSON.parse(init!.body as string)).toEqual({ message: 'Hi there', history: [] });

            const [user, agent] = service.messages();
            expect(user).toEqual(jasmine.objectContaining({ role: 'user', content: 'Hi there' }));
            expect(agent).toEqual(jasmine.objectContaining({ role: 'agent', content: 'Hello', status: 'complete' }));
            expect(service.isStreaming()).toBeFalse();
        });

        it('treats a `done` event like [DONE]', async () => {
            fetchSpy.and.returnValue(Promise.resolve(sseResponse(event('token', { content: 'ok' }), event('done', {}))));
            await service.sendMessage('q');
            expect(lastMessage().status).toBe('complete');
        });

        it('ignores blank input', async () => {
            await service.sendMessage('   ');
            expect(fetchSpy).not.toHaveBeenCalled();
            expect(service.isEmpty()).toBeTrue();
        });

        it('ignores a new message while a reply is streaming', async () => {
            fetchSpy.and.callFake(hangingFetch);
            const first = service.sendMessage('first');

            await service.sendMessage('second');

            expect(fetchSpy).toHaveBeenCalledTimes(1);
            expect(service.messages().length).toBe(2);
            service.stop();
            await first;
        });
    });

    describe('failures', () => {
        it('shows the backend error message from an `error` event', async () => {
            fetchSpy.and.returnValue(
                Promise.resolve(sseResponse(event('error', { message: 'The agent hit an error.' }), DONE))
            );
            await service.sendMessage('q');
            expect(lastMessage()).toEqual(
                jasmine.objectContaining({ status: 'error', error: 'The agent hit an error.' })
            );
        });

        it('flags a stream that closes without [DONE] as cut off, even with partial text', async () => {
            fetchSpy.and.returnValue(Promise.resolve(sseResponse(event('token', { content: 'Half an ans' }))));
            await service.sendMessage('q');
            expect(lastMessage().status).toBe('error');
            expect(lastMessage().error).toContain('connection dropped');
        });

        it('flags an empty reply as an error', async () => {
            fetchSpy.and.returnValue(Promise.resolve(sseResponse(DONE)));
            await service.sendMessage('q');
            expect(lastMessage().error).toBe('The agent returned an empty response.');
        });

        const statuses: [number, string][] = [
            [429, 'Too many requests'],
            [413, 'too long'],
            [503, 'having trouble'],
            [422, 'could not be processed']
        ];
        for (const [status, text] of statuses) {
            it(`maps HTTP ${status} to a friendly message`, async () => {
                fetchSpy.and.returnValue(Promise.resolve(new Response('', { status })));
                await service.sendMessage('q');
                expect(lastMessage().error).toContain(text);
            });
        }

        it('maps a network failure to a connection message', async () => {
            fetchSpy.and.returnValue(Promise.reject(new TypeError('Failed to fetch')));
            await service.sendMessage('q');
            expect(lastMessage().error).toContain('Could not reach the server');
        });

        it('abandons a stream that goes silent for 60 seconds', async () => {
            jasmine.clock().install();
            try {
                fetchSpy.and.callFake(hangingFetch);
                const pending = service.sendMessage('q');

                jasmine.clock().tick(60_000);
                await pending;

                expect(lastMessage().error).toContain('took too long');
            } finally {
                jasmine.clock().uninstall();
            }
        });
    });

    describe('stop', () => {
        it('keeps text that already arrived as a complete answer', async () => {
            let stream!: ReadableStreamDefaultController<Uint8Array>;
            fetchSpy.and.callFake((_url, init) => {
                const body = new ReadableStream<Uint8Array>({
                    start: controller => {
                        stream = controller;
                        // A real fetch errors its body when the request is aborted.
                        init!.signal!.addEventListener('abort', () => controller.error(init!.signal!.reason));
                    }
                });
                return Promise.resolve(new Response(body, { status: 200 }));
            });

            const pending = service.sendMessage('q');
            stream.enqueue(encoder.encode(event('token', { content: 'Partial' })));
            await settle();

            service.stop();
            await pending;

            expect(lastMessage()).toEqual(jasmine.objectContaining({ status: 'complete', content: 'Partial' }));
        });

        it('turns a reply stopped before its first token into a retryable error', async () => {
            fetchSpy.and.callFake(hangingFetch);
            const pending = service.sendMessage('q');

            service.stop();
            await pending;

            expect(lastMessage()).toEqual(jasmine.objectContaining({ status: 'error', error: 'Response stopped.' }));
        });
    });

    describe('retry', () => {
        it('drops the failed turn and re-sends its question', async () => {
            fetchSpy.and.returnValue(Promise.resolve(new Response('', { status: 500 })));
            await service.sendMessage('q');

            fetchSpy.and.returnValue(Promise.resolve(answer('fixed')));
            await service.retry();

            expect(service.messages().map(m => [m.role, m.content, m.status])).toEqual([
                ['user', 'q', 'complete'],
                ['agent', 'fixed', 'complete']
            ]);
        });

        it('does nothing when the last message is not a failure', async () => {
            await converse('q');
            await service.retry();
            expect(fetchSpy).toHaveBeenCalledTimes(1);
        });
    });

    describe('tools and sources', () => {
        it('tracks a tool from start to end and collects its sources', async () => {
            fetchSpy.and.returnValue(
                Promise.resolve(
                    sseResponse(
                        event('tool_start', { tool: 'scrape_cadre_website', id: 'run-1' }),
                        event('tool_end', {
                            tool: 'scrape_cadre_website',
                            id: 'run-1',
                            sources: [{ url: 'https://cadreai.com', title: 'Home' }]
                        }),
                        event('token', { content: 'ok' }),
                        DONE
                    )
                )
            );
            await service.sendMessage('q');

            const agent = lastMessage();
            expect(agent.toolEvents).toEqual([
                {
                    id: 'run-1',
                    tool: 'scrape_cadre_website',
                    status: 'done',
                    sources: [{ type: 'url', label: 'Home', url: 'https://cadreai.com', excerpt: undefined }]
                }
            ]);
        });

        it('reads the different source shapes and drops URL sources without a URL', async () => {
            fetchSpy.and.returnValue(
                Promise.resolve(
                    sseResponse(
                        event('token', { content: 'ok' }),
                        event('sources', {
                            sources: [
                                { url: 'https://a.example', title: 'A' },
                                { content: 'A passage.', name: 'Handbook' },
                                { type: 'document' },
                                { type: 'url', title: 'Broken' },
                                'not an object'
                            ]
                        }),
                        DONE
                    )
                )
            );
            await service.sendMessage('q');

            expect(messageSources(lastMessage())).toEqual([
                { type: 'url', label: 'A', url: 'https://a.example', excerpt: undefined },
                { type: 'document', label: 'Handbook', url: undefined, excerpt: 'A passage.' },
                { type: 'document', label: 'Knowledge base' }
            ]);
        });

        it('closes tools that never got a tool_end when the reply finishes', async () => {
            fetchSpy.and.returnValue(
                Promise.resolve(
                    sseResponse(
                        event('tool_start', { tool: 'query_knowledge_base', id: 'x' }),
                        event('token', { content: 'ok' }),
                        DONE
                    )
                )
            );
            await service.sendMessage('q');
            expect(lastMessage().toolEvents[0].status).toBe('done');
        });
    });

    describe('history', () => {
        it('sends earlier completed turns, oldest first', async () => {
            await converse('one', 'two');
            expect(sentHistory()).toEqual([
                { role: 'user', content: 'one' },
                { role: 'assistant', content: 'answer to one' }
            ]);
        });

        it('leaves out a question whose reply failed', async () => {
            await converse('one');
            fetchSpy.and.returnValue(Promise.resolve(new Response('', { status: 500 })));
            await service.sendMessage('failed');
            await converse('three');

            expect(sentHistory()).toEqual([
                { role: 'user', content: 'one' },
                { role: 'assistant', content: 'answer to one' }
            ]);
        });

        it('keeps at most 20 turns, starting on a user turn', async () => {
            await converse(...Array.from({ length: 12 }, (_, i) => `q${i}`));
            // Sent with the 12th question: 11 earlier exchanges = 22 turns, capped to 20.
            const history = sentHistory();
            expect(history.length).toBe(20);
            expect(history[0]).toEqual({ role: 'user', content: 'q1' });
        });

        it('drops the oldest turns once the character budget is spent', async () => {
            fetchSpy.and.returnValue(Promise.resolve(answer('a'.repeat(10_000))));
            await service.sendMessage('one');
            fetchSpy.and.returnValue(Promise.resolve(answer('b'.repeat(15_000))));
            await service.sendMessage('two');
            await converse('three');

            expect(sentHistory().map(turn => turn.content.slice(0, 3))).toEqual(['two', 'bbb']);
        });

        it('never opens on an answer whose question was cut by the budget', async () => {
            // The answer fits the 24,000-character budget; adding its question would not.
            fetchSpy.and.returnValue(Promise.resolve(answer('a'.repeat(20_001))));
            await service.sendMessage('q'.repeat(4_000));
            await converse('next');

            expect(sentHistory()).toEqual([]);
        });
    });

    describe('clear', () => {
        it('aborts the reply in flight and empties the conversation', async () => {
            fetchSpy.and.callFake(hangingFetch);
            const pending = service.sendMessage('q');

            service.clear();
            await pending;

            expect(service.isEmpty()).toBeTrue();
            expect(service.isStreaming()).toBeFalse();
        });
    });
});
