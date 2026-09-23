import { Source, dedupeSources, sourceDomain } from './source.model';

describe('dedupeSources', () => {
    it('collapses URL sources on the URL, ignoring case and surrounding space', () => {
        const sources: Source[] = [
            { type: 'url', label: 'A', url: 'https://cadreai.com/about' },
            { type: 'url', label: 'B', url: ' HTTPS://CADREAI.COM/about ' }
        ];
        expect(dedupeSources(sources)).toEqual([sources[0]]);
    });

    it('collapses document sources on whitespace-normalised excerpt text', () => {
        const sources: Source[] = [
            { type: 'document', label: 'Doc 1', excerpt: 'Cadre works  with\nPE firms.' },
            { type: 'document', label: 'Doc 2', excerpt: 'cadre works with pe firms.' }
        ];
        expect(dedupeSources(sources)).toEqual([sources[0]]);
    });

    it('keeps a URL and a document with the same text apart, in their original order', () => {
        const sources: Source[] = [
            { type: 'document', label: 'x', excerpt: 'x' },
            { type: 'url', label: 'x', url: 'x' }
        ];
        expect(dedupeSources(sources)).toEqual(sources);
    });
});

describe('sourceDomain', () => {
    it('returns the hostname without www', () => {
        expect(sourceDomain({ type: 'url', label: '', url: 'https://www.cadreai.com/a?b=c' })).toBe(
            'cadreai.com'
        );
    });

    it('returns the raw value when the URL does not parse', () => {
        expect(sourceDomain({ type: 'url', label: '', url: 'not a url' })).toBe('not a url');
    });

    it('returns an empty string when there is no URL', () => {
        expect(sourceDomain({ type: 'document', label: 'Doc' })).toBe('');
    });
});
