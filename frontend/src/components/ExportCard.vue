<template>
  <div class="bg-white border rounded-lg p-4 flex flex-col justify-between">
    <div class="flex gap-3 mb-4 items-start">
      <div class="text-2xl mt-1">{{ icon }}</div>
      <div>
        <h4 class="font-semibold text-sm m-0">{{ title }}</h4>
        <p class="text-xs text-gray-500 m-0">{{ description }}</p>
      </div>
    </div>
    <div class="flex gap-2">
      <Button size="sm" class="flex-1" @click="$emit('push')">⬆ Push</Button>
      <Button size="sm" class="flex-1" theme="green" @click="$emit('download')">⬇ XML</Button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Button } from 'frappe-ui'
defineProps<{
  title: string
  description: string
  icon: string
  type: string
}>()
defineEmits(['push', 'download'])
</script>
