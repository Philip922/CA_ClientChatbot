import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Source } from '../../models/source.model';
import { SourcesPanelComponent } from './sources-panel.component';

const LONG_EXCERPT = 'Cadre works with PE-backed firms across many industries. '.repeat(30);

describe('SourcesPanelComponent', () => {
    let fixture: ComponentFixture<SourcesPanelComponent>;

    async function render(sources: Source[]): Promise<HTMLElement> {
        TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
        fixture = TestBed.createComponent(SourcesPanelComponent);
        fixture.componentRef.setInput('sources', sources);
        // A fixed width so the three-line clamp has something to clip against.
        (fixture.nativeElement as HTMLElement).style.cssText = 'display:block;width:320px';
        document.body.appendChild(fixture.nativeElement);
        await fixture.whenStable();
        return fixture.nativeElement;
    }

    afterEach(() => (fixture.nativeElement as HTMLElement).remove());

    const items = (host: HTMLElement) => Array.from(host.querySelectorAll('li.source'));

    it('links URL sources in a new tab and shows their domain', async () => {
        const host = await render([{ type: 'url', label: 'About', url: 'https://www.cadreai.com/about' }]);

        const link = host.querySelector('a')!;
        expect(link.getAttribute('href')).toBe('https://www.cadreai.com/about');
        expect(link.getAttribute('target')).toBe('_blank');
        expect(host.querySelector('.source__meta')?.textContent).toBe('cadreai.com');
    });

    it('offers "Show more" on a clipped excerpt that follows a URL source', async () => {
        const host = await render([
            { type: 'url', label: 'About', url: 'https://cadreai.com' },
            { type: 'document', label: 'Short', excerpt: 'One line.' },
            { type: 'document', label: 'Long', excerpt: LONG_EXCERPT }
        ]);

        const [url, short, long] = items(host);
        expect(url.querySelector('.source__more')).toBeNull();
        expect(short.querySelector('.source__more')).toBeNull();
        expect(long.querySelector('.source__more')?.textContent?.trim()).toBe('Show more');
    });

    it('expands and collapses an excerpt', async () => {
        const host = await render([{ type: 'document', label: 'Long', excerpt: LONG_EXCERPT }]);
        const excerpt = host.querySelector('.source__excerpt')!;
        const button = () => host.querySelector<HTMLButtonElement>('.source__more')!;

        button().click();
        await fixture.whenStable();
        expect(excerpt.classList).not.toContain('source__excerpt--clamped');
        expect(button().textContent?.trim()).toBe('Show less');

        button().click();
        await fixture.whenStable();
        expect(excerpt.classList).toContain('source__excerpt--clamped');
    });

    it('asks to close on Escape', async () => {
        const host = await render([{ type: 'document', label: 'Doc' }]);
        let closed = 0;
        fixture.componentInstance.closed.subscribe(() => closed++);

        host.querySelector('.panel')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
        expect(closed).toBe(1);
    });
});
