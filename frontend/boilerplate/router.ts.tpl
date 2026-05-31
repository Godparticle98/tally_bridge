import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('@/pages/Home.vue'),
  }, {
    path: '/docs',
    name: "Docs",
    beforeEnter: function(){
      window.open("https://ui.frappe.io/docs/components/alert", "_blank")
    },
    component: () => import('@/pages/Home.vue'),
    },
    {
      path: "/design",
      name: "Design",
      beforeEnter: function(){
          window.open("https://www.figma.com/community/file/1407648399328528443/espresso-by-frappe", "_blank")
      },
      component: () => import('@/pages/Home.vue'),
      },
    {
      path: "/learn",
      name: "Learn",
      beforeEnter: function(){
          window.open("      https://youtube.com/playlist?list=PLQGFK8RiEPSII8N7iwhjqrPTQXq1T42Jl&si=UCp6W5L4esaWmcJH", "_blank")
      },
      component: () => import('@/pages/Home.vue'),
      },
]

let router = createRouter({
  history: createWebHistory("/$app_name"),
  routes,
})

let paths = ["/Docs", "/Design", "/Learn"]
router.afterEach((to, from) => {
  // runs after every successful navigation
  console.log('Entered:', to.fullPath)
  if (paths.includes(to.fullPath)){
       router.replace({ name: 'Home' })
  }
})
export default router