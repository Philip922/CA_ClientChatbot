import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { Message } from '../../models/message.model';
import { MessageBubbleComponent } from './message-bubble.component';

function message(overrides: Partial<Message>): Message {
    return {
        id: 'm1',
        role: 'agent',
        content: '',
        status: 'complete',
        timestamp: new Date(),
        toolEvents: [],
        feedback: null,
        ...overrides
    };
}

describe('MessageBubbleComponent', () => {
    let fixture: ComponentFixture<MessageBubbleComponent>;

    async function render(value: Message, canRetry = false): Promise<HTMLElement> {
        TestBed.configureTestingModule({
            providers: [provideZonelessChangeDetection(), provideHttpClient()]
        });
        fixture = TestBed.createComponent(MessageBubbleComponent);
        fixture.componentRef.setInput('message', value);
        fixture.componentRef.setInput('canRetry', canRetry);
        await fixture.whenStable();
        return fixture.nativeElement;
    }

    it('shows user text as plain text, not markdown', async () => {
        const host = await render(message({ role: 'user', content: '**not bold**' }));
        expect(host.querySelector('.bubble__text')?.textContent).toBe('**not bold**');
        expect(host.querySelector('strong')).toBeNull();
    });

    it('renders agent content as markdown with actions once complete', async () => {
        const host = await render(message({ content: '**bold**' }));
        expect(host.querySelector('.bubble__markdown strong')?.textContent).toBe('bold');
        expect(host.querySelector('app-message-actions')).not.toBeNull();
    });

    it('shows typing dots and no actions before the first token', async () => {
        const host = await render(message({ status: 'streaming' }));
        expect(host.querySelector('app-typing-indicator')).not.toBeNull();
        expect(host.querySelector('app-message-actions')).toBeNull();
    });

    it('offers retry on the latest failed turn', async () => {
        const host = await render(message({ status: 'error', error: 'It broke.' }), true);
        let retries = 0;
        fixture.componentInstance.retry.subscribe(() => retries++);

        expect(host.querySelector('.error__text')?.textContent).toBe('It broke.');
        host.querySelector<HTMLButtonElement>('.error__retry')!.click();
        expect(retries).toBe(1);
    });

    it('shows an older error without a retry button', async () => {
        const host = await render(message({ status: 'error', error: 'It broke.' }), false);
        expect(host.querySelector('.error__text')?.textContent).toBe('It broke.');
        expect(host.querySelector('.error__retry')).toBeNull();
    });
});
