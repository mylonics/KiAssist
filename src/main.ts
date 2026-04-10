import { createApp } from "vue";
import App from "./App.vue";
import 'material-icons/iconfont/material-icons.css';
import 'highlight.js/styles/github-dark.css';

const app = createApp(App);

app.config.errorHandler = (err, instance, info) => {
  console.error(`[Vue Error] ${info}:`, err);
};

app.mount("#app");
