# AskAboutIPL

An AI-powered chat application for everything IPL — teams, players, match stats, records, and more.

## Stack

- **Frontend** — Angular 19, TypeScript, SCSS
- **Backend** — Python (FastAPI), PostgreSQL

## Features

- Claude-style chat UI with TATA IPL dark theme
- Multiple chat sessions with history in the sidebar
- AI assistant powered by an LLM backend
- Real-time typing indicator

## Getting started

### Backend

```bash
cd Backend
python -m venv backendvenv
source backendvenv/bin/activate      # Windows: backendvenv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # fill in your API keys
uvicorn main:app --reload
```

### Frontend

```bash
cd Frontend
npm install
ng serve
```

App runs at `http://localhost:4200`.

## Project structure

```
AskAboutIPL/
├── Backend/
│   ├── main.py          # FastAPI entry point
│   ├── routers/         # API routes (chat, user)
│   ├── services/        # LLM service
│   └── db.py            # Database connection
└── Frontend/
    └── src/app/
        ├── components/
        │   ├── chat/    # Chat window
        │   └── sidebar/ # Session list + navigation
        └── services/
            ├── chat.service.ts    # API calls
            └── session.service.ts # Session state
```
