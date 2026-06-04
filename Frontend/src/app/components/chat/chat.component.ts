import { Component, signal, ViewChild, ElementRef, AfterViewChecked, computed } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { ChatService, ChatMessage } from '../../services/chat.service';
import { SessionService } from '../../services/session.service';
import { Message } from '../../shared/models/chat.model';

@Component({
  selector: 'app-chat',
  imports: [FormsModule, DatePipe],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.scss',
})
export class ChatComponent implements AfterViewChecked {
  @ViewChild('messagesEnd') messagesEnd!: ElementRef;

  inputText = '';
  isLoading = signal(false);
  private shouldScrollToBottom = false;

  messages = computed(() => this.sessionService.activeSession()?.messages ?? []);

  constructor(
    private chatService: ChatService,
    private sessionService: SessionService,
  ) {}

  ngAfterViewChecked() {
    if (this.shouldScrollToBottom) {
      this.messagesEnd?.nativeElement?.scrollIntoView({ behavior: 'smooth' });
      this.shouldScrollToBottom = false;
    }
  }

  sendMessage() {
    const text = this.inputText.trim();
    if (!text || this.isLoading()) return;

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    };

    this.sessionService.addMessage(userMsg);
    this.inputText = '';
    this.isLoading.set(true);
    this.shouldScrollToBottom = true;

    const history: ChatMessage[] = this.messages()
      .slice(0, -1)
      .map(m => ({ role: m.role, content: m.content }));

    this.chatService.sendMessage(text, history).subscribe({
      next: response => {
        this.sessionService.addMessage({
          id: crypto.randomUUID(),
          role: 'assistant',
          content: response.message,
          timestamp: new Date(),
        });
        this.isLoading.set(false);
        this.shouldScrollToBottom = true;
      },
      error: () => {
        this.sessionService.addMessage({
          id: crypto.randomUUID(),
          role: 'assistant',
          content: 'Sorry, something went wrong. Please try again.',
          timestamp: new Date(),
        });
        this.isLoading.set(false);
        this.shouldScrollToBottom = true;
      },
    });
  }

  onKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendMessage();
    }
  }

  autoResize(event: Event) {
    const el = event.target as HTMLTextAreaElement;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
  }
}
