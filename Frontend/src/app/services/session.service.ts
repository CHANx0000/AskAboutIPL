import { Injectable, signal, computed } from '@angular/core';
import { Message } from '../shared/models/chat.model';

export interface ChatSession {
  id: string;
  title: string;
  date: Date;
  messages: Message[];
}

function makeWelcomeMsg(): Message {
  return {
    id: crypto.randomUUID(),
    role: 'assistant',
    content: "Hello! Ask me anything about IPL — teams, players, stats, matches, records, and more!",
    timestamp: new Date(),
  };
}

@Injectable({ providedIn: 'root' })
export class SessionService {
  private _sessions = signal<ChatSession[]>([]);
  private _activeId = signal<string>('');

  readonly sessions = this._sessions.asReadonly();
  readonly activeId = this._activeId.asReadonly();
  readonly activeSession = computed(() =>
    this._sessions().find(s => s.id === this._activeId())
  );

  constructor() {
    this.newSession();
  }

  newSession(): void {
    const session: ChatSession = {
      id: crypto.randomUUID(),
      title: 'New Chat',
      date: new Date(),
      messages: [makeWelcomeMsg()],
    };
    this._sessions.update(s => [session, ...s]);
    this._activeId.set(session.id);
  }

  switchSession(id: string): void {
    this._activeId.set(id);
  }

  addMessage(message: Message): void {
    const activeId = this._activeId();
    this._sessions.update(sessions =>
      sessions.map(s => {
        if (s.id !== activeId) return s;
        const userCount = s.messages.filter(m => m.role === 'user').length;
        const newTitle =
          message.role === 'user' && userCount === 0
            ? message.content.slice(0, 45)
            : s.title;
        return { ...s, title: newTitle, messages: [...s.messages, message] };
      })
    );
  }
}
