import { createApp } from "vue";
import { createPinia } from "pinia";
import {
  ElAlert,
  ElButton,
  ElTable,
  ElTableColumn,
  ElTag,
  ElSwitch,
} from "element-plus";
import "element-plus/es/components/alert/style/css";
import "element-plus/es/components/button/style/css";
import "element-plus/es/components/table/style/css";
import "element-plus/es/components/tag/style/css";
import "element-plus/es/components/switch/style/css";
import "./style.css";
import App from "./App.vue";
import { router } from "./router";

const app = createApp(App).use(createPinia()).use(router);
for (const component of [
  ElAlert,
  ElButton,
  ElTable,
  ElTableColumn,
  ElTag,
  ElSwitch,
])
  app.component(component.name!, component);
app.mount("#app");
