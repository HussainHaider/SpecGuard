# Two stages: build the bundle with the full toolchain, serve it from a bare nginx.
# The runtime image has no node, no npm and no source — a static bundle needs none of
# them, and shipping them would be a larger attack surface for no benefit.
FROM node:22-alpine AS build
WORKDIR /app

# Dependencies first, so a source change does not re-resolve the tree.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# Vite inlines env at build time, so the API URL is baked into the bundle here rather
# than read at runtime. A different API host means a rebuild; that is the trade a
# static bundle makes.
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_LANGSMITH_URL=https://smith.langchain.com
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_LANGSMITH_URL=$VITE_LANGSMITH_URL
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
# Two client-side routes, so any path that is not a file must return index.html —
# otherwise a reload on /ops is a 404 from nginx rather than a page.
RUN printf 'server {\n\
  listen 5173;\n\
  root /usr/share/nginx/html;\n\
  location / { try_files $uri $uri/ /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 5173
