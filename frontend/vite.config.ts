import {defineConfig} from 'vite';
export default defineConfig({server:{proxy:{'/api':'http://127.0.0.1:8000','/naive-api':{target:'http://127.0.0.1:8011',rewrite:(path)=>path.replace(/^\/naive-api/,'')}}},build:{outDir:'dist'}});
