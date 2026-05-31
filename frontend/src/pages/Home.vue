<template>
  <div class="max-w-5xl py-8 mx-auto px-4">
    <!-- Header -->
    <div class="flex justify-between items-start mb-8">
      <div>
        <h2 class="text-3xl font-bold mb-1">🔗 Tally Bridge Dashboard</h2>
        <p class="text-gray-600">Sync ERPNext data with Tally Prime 4.x. Push directly or download XML to import manually.</p>
      </div>
      <div class="flex gap-2">
        <Button icon-left="settings" @click="openSettings">Settings</Button>
        <Button icon-left="list" @click="openLogs">All Logs</Button>
      </div>
    </div>

    <!-- Connection Status -->
    <div class="bg-white border rounded-lg p-5 mb-6 shadow-sm flex items-center justify-between">
      <div class="flex items-center gap-3">
        <span class="text-2xl">⚡</span>
        <h3 class="text-lg font-semibold m-0">Tally Connection</h3>
      </div>
      <div class="flex items-center gap-3">
        <Badge :variant="connStatusVariant">{{ connStatusText }}</Badge>
        <Button :loading="connTest.loading" @click="testConnection">Test Connection</Button>
      </div>
    </div>

    <!-- Date Range -->
    <div class="bg-white border rounded-lg p-5 mb-6 shadow-sm">
      <div class="flex items-center gap-2 mb-4">
        <span class="text-xl">📅</span>
        <h3 class="text-lg font-semibold m-0">Date Range</h3>
        <span class="text-gray-500 text-sm">(applies to Invoices, Payments, Journals, Bank)</span>
      </div>
      <div class="flex gap-4">
        <div class="flex flex-col gap-1 w-48">
          <label class="text-sm text-gray-600 font-medium">From Date</label>
          <Input type="date" v-model="fromDate" />
        </div>
        <div class="flex flex-col gap-1 w-48">
          <label class="text-sm text-gray-600 font-medium">To Date</label>
          <Input type="date" v-model="toDate" />
        </div>
      </div>
      <div class="mt-4 p-3 bg-blue-50 border border-blue-200 rounded text-blue-800 text-sm flex gap-4">
        <b>How to export:</b>
        <span><Badge variant="solid" theme="blue">⬆ Push to Tally</Badge> sends directly via network.</span>
        <span><Badge variant="solid" theme="green">⬇ Download XML</Badge> generates a file to import manually.</span>
      </div>
    </div>

    <!-- Masters -->
    <div class="text-xs font-bold uppercase tracking-wider text-gray-400 mb-3 mt-8">📚 Master Data</div>
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      <ExportCard
        v-for="m in masterTypes"
        :key="m.type"
        :title="m.title"
        :description="m.description"
        :icon="m.icon"
        :type="m.type"
        @push="runExport(m.type, true)"
        @download="runExport(m.type, false)"
      />
    </div>

    <!-- Transactions -->
    <div class="text-xs font-bold uppercase tracking-wider text-gray-400 mb-3 mt-8">🧾 Transactions</div>
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      <ExportCard
        v-for="t in transactionTypes"
        :key="t.type"
        :title="t.title"
        :description="t.description"
        :icon="t.icon"
        :type="t.type"
        @push="runExport(t.type, true)"
        @download="runExport(t.type, false)"
      />
    </div>

    <!-- Full Export -->
    <div class="bg-gradient-to-br from-green-50 to-blue-50 border border-green-200 rounded-lg p-5 mb-6 shadow-sm flex items-center">
      <div class="text-3xl mr-4">🚀</div>
      <div class="flex-1">
        <h4 class="text-base font-semibold m-0">Full Export</h4>
        <p class="text-sm text-gray-600 m-0">Export all masters + transactions in one shot</p>
      </div>
      <div class="flex gap-2">
        <Button variant="solid" @click="runExport('all', true)">⬆ Push All</Button>
        <Button variant="solid" theme="green" @click="runExport('all', false)">⬇ Download Full XML</Button>
      </div>
    </div>

    <!-- Import -->
    <div class="bg-white border rounded-lg p-5 mb-6 shadow-sm">
      <div class="flex items-center gap-2 mb-3">
        <span class="text-xl">📥</span>
        <h3 class="text-lg font-semibold m-0">Import Data from Tally</h3>
      </div>
      <p class="text-sm text-gray-600 mb-4">Upload a Tally Master or Transaction XML file.</p>
      <div class="flex gap-4 items-center">
        <input type="file" ref="fileInput" accept=".xml" class="border p-2 rounded w-full max-w-md" />
        <Button variant="solid" @click="importTallyXML">Start Import</Button>
      </div>
      <div v-if="importStatus" class="mt-3 text-sm font-medium" :class="importStatusClass">
        {{ importStatus }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Button, Badge, Input, createResource } from 'frappe-ui'
import ExportCard from '../components/ExportCard.vue'

const fromDate = ref('')
const toDate = ref(new Date().toISOString().split('T')[0])

// Initialize fromDate to 30 days ago
const fd = new Date()
fd.setDate(fd.getDate() - 30)
fromDate.value = fd.toISOString().split('T')[0]

const masterTypes = [
  { type: 'uoms', title: 'Units of Measure', description: 'All UOMs', icon: '📐' },
  { type: 'stock_items', title: 'Stock Items', description: 'Items as stock items', icon: '📦' },
  { type: 'chart_of_accounts', title: 'Chart of Accounts', description: 'GL accounts', icon: '📊' },
  { type: 'parties', title: 'Customers & Suppliers', description: 'Debtors/Creditors', icon: '👥' },
]

const transactionTypes = [
  { type: 'sales_invoices', title: 'Sales Invoices', description: 'Sales vouchers', icon: '🧾' },
  { type: 'purchase_invoices', title: 'Purchase Invoices', description: 'Purchase vouchers', icon: '🛒' },
  { type: 'payment_in', title: 'Payment In', description: 'Receipt vouchers', icon: '📥' },
  { type: 'payment_out', title: 'Payment Out', description: 'Payment vouchers', icon: '📤' },
  { type: 'journal_entries', title: 'Journal Entries', description: 'Journal vouchers', icon: '📓' },
  { type: 'bank_transactions', title: 'Bank Transactions', description: 'Bank statements', icon: '🏦' },
]

const connStatusVariant = ref('gray')
const connStatusText = ref('Not tested')

const connTest = createResource({
  url: 'tally_bridge.api.export.test_tally_connection',
  onSuccess(data) {
    if (data.success) {
      connStatusVariant.value = 'solid-green'
      connStatusText.value = 'Connected'
    } else {
      connStatusVariant.value = 'solid-red'
      connStatusText.value = 'Failed'
    }
  },
  onError() {
    connStatusVariant.value = 'solid-red'
    connStatusText.value = 'Error'
  }
})

function testConnection() {
  connStatusText.value = 'Testing...'
  connStatusVariant.value = 'subtle'
  connTest.fetch()
}

function openSettings() {
  window.location.href = '/app/tally-settings'
}

function openLogs() {
  window.location.href = '/app/tally-export-log'
}

function runExport(type: string, pushToTally: boolean) {
  fetch('/api/method/tally_bridge.api.export.export_' + type, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      from_date: fromDate.value,
      to_date: toDate.value,
      push_to_tally_flag: pushToTally
    })
  }).then(res => res.json()).then(res => {
    if(res.message && res.message.success && !pushToTally && res.message.xml) {
      const blob = new Blob([res.message.xml], { type: "application/xml" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `tally_${type}.xml`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }
  })
}

const fileInput = ref<HTMLInputElement | null>(null)
const importStatus = ref('')
const importStatusClass = ref('text-blue-600')

function importTallyXML() {
  if (!fileInput.value?.files?.length) {
    alert("Please select an XML file to import.")
    return
  }
  const file = fileInput.value.files[0]
  importStatus.value = 'Reading file...'
  importStatusClass.value = 'text-blue-600'

  const reader = new FileReader()
  reader.onload = (e) => {
    importStatus.value = 'Processing data in backend... this may take a while.'
    fetch('/api/method/tally_bridge.api.import_data.process_tally_xml', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ xml_data: e.target?.result })
    }).then(res => res.json()).then(res => {
      if (res.message?.success) {
        importStatus.value = '✅ Import successful! ' + res.message.message
        importStatusClass.value = 'text-green-600'
      } else {
        importStatus.value = '❌ Import failed: ' + (res.message?.error || 'Unknown error')
        importStatusClass.value = 'text-red-600'
      }
    }).catch(err => {
      importStatus.value = '❌ Request failed.'
      importStatusClass.value = 'text-red-600'
    })
  }
  reader.readAsText(file)
}
</script>
