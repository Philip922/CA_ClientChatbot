import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { InputBarComponent } from './input-bar.component';

describe('InputBarComponent', () => {
    let fixture: ComponentFixture<InputBarComponent>;
    let textarea: HTMLTextAreaElement;
    let sent: string[];

    beforeEach(async () => {
        TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
        fixture = TestBed.createComponent(InputBarComponent);
        sent = [];
        fixture.componentInstance.send.subscribe(text => sent.push(text));
        await fixture.whenStable();
        textarea = fixture.nativeElement.querySelector('textarea');
    });

    async function type(text: string): Promise<void> {
        textarea.value = text;
        textarea.dispatchEvent(new Event('input'));
        await fixture.whenStable();
    }

    async function press(init: KeyboardEventInit): Promise<KeyboardEvent> {
        const event = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true, ...init });
        textarea.dispatchEvent(event);
        await fixture.whenStable();
        return event;
    }

    const sendButton = (): HTMLButtonElement => fixture.nativeElement.querySelector('.composer__send');

    it('sends the trimmed draft on Enter and clears it', async () => {
        await type('  hello  ');
        const event = await press({});

        expect(sent).toEqual(['hello']);
        expect(event.defaultPrevented).toBeTrue();
        expect(textarea.value).toBe('');
    });

    it('inserts a newline on Shift+Enter instead of sending', async () => {
        await type('hello');
        const event = await press({ shiftKey: true });

        expect(sent).toEqual([]);
        expect(event.defaultPrevented).toBeFalse();
    });

    it('does not send while an IME composition is being committed', async () => {
        await type('こんにちは');
        await press({ isComposing: true });
        expect(sent).toEqual([]);
    });

    it('does not send blank drafts', async () => {
        await type('   ');
        await press({});
        expect(sent).toEqual([]);
        expect(sendButton().disabled).toBeTrue();
    });

    it('keeps the draft while a reply streams and offers stop instead of send', async () => {
        let stops = 0;
        fixture.componentInstance.stop.subscribe(() => stops++);
        fixture.componentRef.setInput('streaming', true);
        await type('next question');
        await press({});

        expect(sent).toEqual([]);
        expect(textarea.value).toBe('next question');

        sendButton().click();
        expect(stops).toBe(1);
    });

    it('shrinks back to one line after sending a multi-line message', async () => {
        const oneLine = textarea.offsetHeight;
        await type('1\n2\n3\n4\n5\n6');
        expect(textarea.offsetHeight).toBeGreaterThan(oneLine);

        await press({});

        expect(textarea.offsetHeight).toBe(oneLine);
    });

    it('caps its growth and scrolls internally past four lines', async () => {
        await type('1\n2\n3\n4');
        const fourLines = textarea.offsetHeight;
        await type('1\n2\n3\n4\n5\n6\n7\n8');

        expect(textarea.offsetHeight).toBe(fourLines);
        expect(textarea.style.overflowY).toBe('auto');
    });

    it('shows the character counter only near the limit', async () => {
        await type('x'.repeat(100));
        expect(fixture.nativeElement.querySelector('.composer__count')).toBeNull();

        await type('x'.repeat(3_700));
        expect(fixture.nativeElement.querySelector('.composer__count')?.textContent).toContain('3700/4000');
    });
});
