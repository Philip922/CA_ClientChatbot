import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { LucideBookOpen, LucideGlobe, LucideLifeBuoy } from '@lucide/angular';
import { ToolEvent, toolLabel } from '../../models/message.model';

/**
 * Narrates what the agent is doing before the answer arrives. Shows the
 * currently running tool and nothing once every tool event is done — at that
 * point the tokens themselves are the feedback.
 */
@Component({
    selector: 'app-tool-indicator',
    standalone: true,
    imports: [LucideBookOpen, LucideGlobe, LucideLifeBuoy],
    templateUrl: './tool-indicator.component.html',
    styleUrl: './tool-indicator.component.scss',
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class ToolIndicatorComponent {
    readonly events = input.required<ToolEvent[]>();

    protected readonly running = computed(
        () => this.events().find(event => event.status === 'running') ?? null
    );

    protected readonly label = computed(() => {
        const event = this.running();
        return event ? toolLabel(event.tool) : '';
    });
}
