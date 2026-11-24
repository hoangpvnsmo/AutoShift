import {createApp} from 'vue'
import {createPinia} from 'pinia'
import {createRouter,createWebHistory} from 'vue-router'
import ElementPlus from 'element-plus'
import vi from 'element-plus/es/locale/lang/vi'
import 'element-plus/dist/index.css'
import App from './App.vue'
import './style.css'
const router=createRouter({history:createWebHistory(),routes:['/','/requests','/employees','/rules','/annual','/history'].map(path=>({path,component:{template:'<span />'}}))})
createApp(App).use(createPinia()).use(router).use(ElementPlus,{locale:vi}).mount('#app')
