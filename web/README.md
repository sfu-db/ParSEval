# ParSEval WebUI

This repository contains the WebUI for ParSEval, providing an interactive frontend for text-to-sql experiment management, dataset exploration, and result visualization.

## Overview

- **Frontend:** [Next.js](https://nextjs.org/) (React, TypeScript)
- **Backend:** [Flask](https://flask.palletsprojects.com/) (Python)
- **Features:**
  - Project and experiment management
  - Dataset upload and exploration[not for demo]
  - SQL query equivalence checking
  - Interactive charts and visualizations
  - Playground for manual SQL comparison[not for demo]

## Getting Started

### Prerequisites

- Node.js
- Python 3.8+
- [pnpm](https://pnpm.io/) (or npm/yarn)

### Backend (Flask)

1. Clone the repository and navigate to the backend directory (if separate).
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start the Flask server:
   ```bash
   flask run
   ```
   By default, the backend runs on `http://localhost:5000`.

### Frontend (Next.js)

1. Navigate to the `web` directory:
   ```bash
   cd web
   ```
2. Install dependencies:
   ```bash
   pnpm install
   # or
   npm install
   # or
   yarn install
   ```
3. Start the development server:
   ```bash
   pnpm dev
   # or
   npm run dev
   # or
   yarn dev
   ```
   The frontend runs on `http://localhost:3000` by default.

## Project Structure

- `web/` — Next.js frontend
- `src/` — Core logic and utilities
- `docs/` — Documentation
- `docker/` — Docker setup

## Acknowledgements

- [Next.js](https://nextjs.org/)
- [Flask](https://flask.palletsprojects.com/)
- [Recharts](https://recharts.org/)
- [Tailwind CSS](https://tailwindcss.com/)
