/**
 * Mock of the FastAPI backend, for developing the UI before the real agent is up.
 *
 *   node tools/mock-server.mjs        # listens on :8000, matching proxy.conf.json
 *
 * It implements exactly the contract `ChatService` expects, so it doubles as the
 * spec for the real endpoints:
 *
 *   POST /chat      { message, history: [{ role, content }] }
 *                   → text/event-stream of:
 *                       event: tool_start  data: { "id", "tool" }
 *                       event: tool_end    data: { "id", "tool", "sources": [...] }
 *                       event: token       data: { "content": "…" }
 *                       event: sources     data: { "sources": [...] }   (optional)
 *                       event: done        data: {}
 *                       event: error       data: { "message": "…" }
 *   POST /feedback  { messageId, rating, content } → 204
 */
import { createServer } from 'node:http';

const PORT = Number(process.env.PORT ?? 8000);

const SOURCES = [
    {
        type: 'url',
        label: 'Cadre AI — Industries',
        url: 'https://cadreai.com/industries'
    },
    {
        type: 'document',
        label: 'Knowledge base — engagement model',
        excerpt:
            'Cadre works with PE-backed firms across construction, real estate, and professional ' +
            'services. Engagements start with a two-week discovery sprint that maps the highest-value ' +
            'workflows, followed by a production pilot scoped to a single team. Most clients reach a ' +
            'measurable outcome inside one quarter.'
    }
];

const ANSWER =
    '## Industries we work with\n\n' +
    'Cadre works with companies across **professional services**, **private equity**, ' +
    '**real estate**, and **construction**.\n\n' +
    'A typical engagement looks like:\n\n' +
    '1. A two-week discovery sprint\n' +
    '2. A production pilot with one team\n' +
    '3. Rollout, with `measurable` outcomes inside a quarter\n\n' +
    'Want an introduction to someone on the team?';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function send(res, event, data) {
    res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

async function readBody(req) {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    try {
        return JSON.parse(Buffer.concat(chunks).toString() || '{}');
    } catch {
        return {};
    }
}

async function handleChat(req, res) {
    const { message = '' } = await readBody(req);

    res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
        // Nginx/CloudFront must not buffer a stream.
        'X-Accel-Buffering': 'no'
    });

    // `fail` in the prompt exercises the error + retry path.
    if (/\bfail\b/i.test(message)) {
        await sleep(600);
        send(res, 'error', { message: 'The agent is unavailable right now.' });
        res.end();
        return;
    }

    send(res, 'tool_start', { id: 'kb-1', tool: 'query_knowledge_base' });
    await sleep(900);
    send(res, 'tool_end', { id: 'kb-1', tool: 'query_knowledge_base', sources: [SOURCES[1]] });

    send(res, 'tool_start', { id: 'web-1', tool: 'scrape_cadre_website' });
    await sleep(900);
    send(res, 'tool_end', { id: 'web-1', tool: 'scrape_cadre_website', sources: [SOURCES[0]] });

    // Chunked the way a model streams — whitespace kept with the preceding word.
    for (const token of ANSWER.match(/\s*\S+/g) ?? []) {
        send(res, 'token', { content: token });
        await sleep(28);
    }

    send(res, 'done', {});
    res.end();
}

const server = createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    if (req.method === 'OPTIONS') {
        res.writeHead(204).end();
        return;
    }
    if (req.method === 'POST' && req.url === '/chat') {
        await handleChat(req, res);
        return;
    }
    if (req.method === 'POST' && req.url === '/feedback') {
        console.log('feedback', await readBody(req));
        res.writeHead(204).end();
        return;
    }
    res.writeHead(404).end();
});

server.listen(PORT, () => console.log(`mock backend listening on http://localhost:${PORT}`));
