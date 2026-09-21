import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { LucideRotateCw, LucideTriangleAlert } from '@lucide/angular';
import { MarkdownPipe } from '../../../../core/pipes/markdown.pipe';
import { Message } from '../../models/message.model';
import { MessageActionsComponent } from '../message-actions/message-actions.component';
import { ToolIndicatorComponent } from '../tool-indicator/tool-indicator.component';
import { TypingIndicatorComponent } from '../typing-indicator/typing-indicator.component';

/**
 * One turn. User messages are plain text on a dark bubble, right-aligned;
 * agent messages are rendered markdown on a light bubble, left-aligned.
 */
@Component({
    selector: 'app-message-bubble',
    standalone: true,
    imports: [
        LucideRotateCw,
        LucideTriangleAlert,
        MarkdownPipe,
        MessageActionsComponent,
        ToolIndicatorComponent,
        TypingIndicatorComponent
    ],
    templateUrl: './message-bubble.component.html',
    styleUrl: './message-bubble.component.scss',
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class MessageBubbleComponent {
    readonly message = input.required<Message>();
    readonly retry = output<void>();

    protected readonly isUser = computed(() => this.message().role === 'user');
    protected readonly isStreaming = computed(() => this.message().status === 'streaming');
    protected readonly isComplete = computed(() => this.message().status === 'complete');
    protected readonly hasError = computed(() => this.message().status === 'error');

    /** Between send and the first token there is nothing to show but the dots. */
    protected readonly isAwaitingFirstToken = computed(
        () => this.isStreaming() && this.message().content.length === 0
    );
}
