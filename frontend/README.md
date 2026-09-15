# CAD Intelligence Frontend

React + TypeScript + Vite workbench for the CAD Knowledge Base API.

## Start

From the project root:

~~~bash
cd frontend
cp .env.example .env
npm install
npm run dev
~~~

Open http://127.0.0.1:5173.

The API base URL defaults to http://127.0.0.1:8000 and can be changed with
VITE_API_BASE_URL.

## First version

- /drawings: drawing library and processing statistics
- /drawings/:drawingKey: PNG/CAD/OCR/Vision viewer with layer toggles
- /uploads/new: DWG/PNG upload and background processing polling
- /drawings/:drawingKey/history: revision history
- right-side CAD Assistant: POST /api/chat
