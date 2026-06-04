import { Component, computed } from '@angular/core';
import { DatePipe } from '@angular/common';
import { SessionService } from '../../services/session.service';

@Component({
  selector: 'app-sidebar',
  imports: [DatePipe],
  templateUrl: './sidebar.component.html',
  styleUrl: './sidebar.component.scss',
})
export class SidebarComponent {
  sessions = computed(() => this.sessionService.sessions());
  activeId = computed(() => this.sessionService.activeId());

  constructor(private sessionService: SessionService) {}

  newChat(): void {
    this.sessionService.newSession();
  }

  switchChat(id: string): void {
    this.sessionService.switchSession(id);
  }
}
