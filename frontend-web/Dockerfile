FROM nginx:alpine

WORKDIR /usr/share/nginx/html

COPY index.html .
COPY chat.html .
COPY chat.js .
COPY webrtc.js .

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]