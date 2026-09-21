import { ChangeDetectionStrategy, Component } from '@angular/core';
import { ChatComponent } from './features/chat/chat.component';

@Component({
    selector: 'app-root',
    standalone: true,
    imports: [ChatComponent],
    template: '<app-chat />',
    changeDetection: ChangeDetectionStrategy.OnPush
})
export class App {}
