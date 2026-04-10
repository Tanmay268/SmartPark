# Deployment Guide

This project is deployed in two parts:

- Frontend on Vercel
- Backend on Render

## Backend on Render

The backend service definition is already prepared in [render.yaml](d:/Downloads/smart-parking-system/smart-parking/render.yaml).
The backend is pinned to Python 3.11.9 via [backend/runtime.txt](d:/Downloads/smart-parking-system/smart-parking/backend/runtime.txt).

### Steps
1. Push the repository to GitHub.
2. In Render, choose `New +` -> `Blueprint`.
3. Connect your GitHub repository.
4. Render will detect [render.yaml](d:/Downloads/smart-parking-system/smart-parking/render.yaml).
5. Create the service.

### Important environment values
Set or confirm these in Render:

- `FRONTEND_URL=https://your-frontend-domain.vercel.app`
- `SECRET_KEY=<strong-random-secret>` if you want to override the generated one
- `SIMULATION_MODE=true`
- `SERIAL_LISTENER_ENABLED=false`

The cloud backend cannot access your local Arduino, IR sensor, LCD, servo, or webcam, so hardware stays disabled on Render.

### Backend URL
After deployment, Render will give you a URL like:

```text
https://smart-parking-backend.onrender.com
```

## Frontend on Vercel

### Steps
1. In Vercel, choose `Add New Project`.
2. Import the same GitHub repository.
3. Set the root directory to `frontend`.
4. If prompted, use:

```text
Build Command: npm run build
Output Directory: build
```

### Frontend environment variable
Set this in Vercel before deploying:

```env
REACT_APP_API_URL=https://your-render-backend.onrender.com
```

## Connect the Two Deployments

After the frontend is deployed:

1. Copy the Vercel frontend URL.
2. Go back to Render.
3. Update:

```env
FRONTEND_URL=https://your-frontend-domain.vercel.app
```

4. Redeploy the backend.

This makes CORS work correctly for the deployed frontend.

## Local Hardware Note

Hardware-triggered entry flow only works on the machine physically connected to:

- Arduino
- IR sensor
- LCD
- Servo
- Camera

So:

- use local backend for hardware testing
- use Render backend for online demo/API hosting

## Local Frontend Against Deployed Backend

If you want to keep the frontend local but point it at Render, create `frontend/.env.local`:

```env
REACT_APP_API_URL=https://your-render-backend.onrender.com
```

Then restart the frontend.

## Recommended Order

1. Deploy backend on Render
2. Copy Render backend URL
3. Deploy frontend on Vercel with `REACT_APP_API_URL`
4. Copy Vercel frontend URL
5. Update Render `FRONTEND_URL`
6. Redeploy backend

## Official Docs

- Vercel docs: https://vercel.com/docs
- Render docs: https://render.com/docs
