# AIsleEye dashboard

The Next.js 16 (App Router, React 19, Tailwind 4) front end for AIsleEye. See the [root README](../README.md) for the whole project.

```bash
npm install
npm run dev      # http://localhost:3001 (port 3000 is left to the optional local Langfuse)
npm test         # Vitest + React Testing Library
npm run lint
npm run build
```

The browser only talks to this origin: `next.config.ts` proxies `/api/*` to the fixed `BACKEND_URL` (default `http://127.0.0.1:8000`, read at build time), so the backend's HttpOnly session cookie covers API calls and PDF links. Leave `NEXT_PUBLIC_API_URL` unset (remove it from any old `.env.local`): pointing the browser straight at the backend would drop the cookie. Start the backend with `DEMO_SIGN_IN_ENABLED=true` (or run `scripts/demo.sh`) and pick a role on the first screen; see "Authentication and RBAC" in the root README.
