/** Where a citation came from: a scraped web page or a knowledge base chunk. */
export type SourceType = 'url' | 'document';

export interface Source {
    type: SourceType;
    /** Page title for `url` sources, document or chunk name for `document` sources. */
    label: string;
    /** Present on `url` sources only. */
    url?: string;
    /** Present on `document` sources only — the relevant passage. */
    excerpt?: string;
}

/**
 * Identity used to deduplicate sources across tool events. URLs collapse on the
 * URL itself; document chunks collapse on their excerpt text, which stands in for
 * the content hash since the backend does not send one.
 */
export function sourceKey(source: Source): string {
    if (source.type === 'url') return `url:${(source.url ?? source.label).trim().toLowerCase()}`;
    return `doc:${(source.excerpt ?? source.label).replace(/\s+/g, ' ').trim().toLowerCase()}`;
}

export function dedupeSources(sources: Source[]): Source[] {
    const seen = new Set<string>();
    const result: Source[] = [];
    for (const source of sources) {
        const key = sourceKey(source);
        if (seen.has(key)) continue;
        seen.add(key);
        result.push(source);
    }
    return result;
}

/** Hostname shown as muted metadata under a URL source. */
export function sourceDomain(source: Source): string {
    if (!source.url) return '';
    try {
        return new URL(source.url).hostname.replace(/^www\./, '');
    } catch {
        return source.url;
    }
}
