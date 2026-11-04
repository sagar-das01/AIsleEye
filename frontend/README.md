# AIsleEye dashboard

The Next.js 16 (App Router, React 19, Tailwind 4) front end for AIsleEye. See the [root README](../README.md) for the whole project.

```bash
npm install
npm run dev      # http://localhost:3001 (port 3000 is left to the optional local Langfuse)
npm test         # Vitest + React Testing Library
npm run lint
npm run build
```

The dashboard talks to the backend at `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`; set it in `.env.local`). It has no sign-in yet, so start the backend with `scripts/demo.sh` or the local demo switch described in the root README.
