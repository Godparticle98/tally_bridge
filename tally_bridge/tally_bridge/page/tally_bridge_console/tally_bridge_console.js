frappe.pages["tally-bridge-console"].on_page_load = function(wrapper) {
    new TallyBridgeConsole(wrapper);
};

class TallyBridgeConsole {
    constructor(wrapper) {
        this.page = frappe.ui.make_app_page({parent: wrapper, title: __("Tally Bridge Console"), single_column: true});
        this.wrapper = $(wrapper);
        this.connection = null;
        this.make();
        this.refresh();
    }

    make() {
        this.page.set_primary_action(__("Reconcile Now"), () => this.reconcile());
        this.page.add_inner_button(__("Refresh"), () => this.refresh());
        this.page.add_inner_button(__("Provision All Create-Required"), () => this.provision_all());
        this.wrapper.find(".layout-main-section").html(`
            <div class="tb-console">
                <div class="tb-toolbar"><div class="tb-connection"></div><div class="tb-status"></div></div>
                <div class="tb-cards"></div>
                <div class="tb-grid">
                    <div class="tb-panel">
                        <div class="tb-panel-head"><div><h4>Reconciliation</h4><p>ERPNext masters compared with TallyPrime.</p></div><button class="btn btn-default btn-sm tb-reconcile">Reconcile</button></div>
                        <div class="tb-summary"></div><div class="tb-types"></div>
                    </div>
                    <div class="tb-panel">
                        <div class="tb-panel-head"><div><h4>Master Provisioning</h4><p>Queue Create Required masters for the Windows Agent.</p></div></div>
                        <div class="tb-provision-controls"></div><div class="tb-provision-result"></div>
                    </div>
                </div>
                <div class="tb-panel"><div class="tb-panel-head"><div><h4>Sync Queue</h4><p>ERPNext → Agent → TallyPrime.</p></div></div><div class="tb-queue-grid"></div></div>
            </div>`);
        this.add_styles();
        this.wrapper.find(".tb-reconcile").on("click", () => this.reconcile());
    }

    call(method, args) {
        return new Promise((resolve, reject) => frappe.call({
            method: "tally_bridge.api." + method, args: args || {},
            callback: r => resolve(r.message || {}), error: reject
        }));
    }

    async refresh() {
        try {
            const data = await this.call("get_dashboard", {connection: this.connection});
            this.connection = data.connection || this.connection;
            this.render(data);
        } catch (e) {
            frappe.msgprint({title: __("Tally Bridge"), message: __("Unable to load dashboard."), indicator: "red"});
        }
    }

    render(data) {
        const connections = data.connections || [];
        this.wrapper.find(".tb-connection").html(`
            <label>Connection</label>
            <select class="form-control tb-connection-select">
                ${connections.map(x => `<option value="${frappe.utils.escape_html(x.name)}" ${x.name === data.connection ? "selected" : ""}>${frappe.utils.escape_html(x.tally_company_name || x.name)}</option>`).join("")}
            </select>`);
        this.wrapper.find(".tb-connection-select").on("change", e => { this.connection = e.target.value; this.refresh(); });

        const s = data.summary || {};
        const cards = [
            ["ERP Masters", s.erp_master_count || 0, ""], ["Matched", s.matched || 0, "ok"],
            ["Shared Mappings", s.shared_mappings ?? s.shared_mapping ?? 0, "warn"],
            ["Create Required", s.create_required || 0, "danger"], ["Needs Review", s.needs_review || 0, "warn"],
            ["Queue", Object.values(data.queue || {}).reduce((a,b) => a + b, 0), ""]
        ];
        this.wrapper.find(".tb-cards").html(cards.map(c => `<div class="tb-card ${c[2]}"><span>${c[0]}</span><strong>${c[1]}</strong></div>`).join(""));
        const recon = data.reconciliation;
        this.wrapper.find(".tb-status").html(recon ? `<span class="indicator-pill">${frappe.utils.escape_html(recon.status)}</span>` : "<span>No reconciliation yet</span>");

        this.wrapper.find(".tb-summary").html(`<div class="tb-inline">
            <span>Exact <b>${s.exact_matches || 0}</b></span><span>Shared records <b>${s.shared_mapping_records || 0}</b></span>
            <span>Create required <b>${s.create_required || 0}</b></span><span>Needs review <b>${s.needs_review || 0}</b></span>
        </div>`);
        const types = s.by_object_type || {};
        this.wrapper.find(".tb-types").html(`<table class="table table-bordered"><thead><tr><th>Object</th><th>ERPNext</th><th>Tally</th><th>Matched</th><th>Shared</th><th>Create</th><th>Review</th></tr></thead><tbody>
            ${Object.entries(types).map(([k,v]) => `<tr><td><b>${k}</b></td><td>${v.erp_count}</td><td>${v.tally_count}</td><td>${v.matched}</td><td>${v.shared_mappings || 0}</td><td class="tb-danger">${v.create_required}</td><td>${v.needs_review}</td></tr>`).join("")}
        </tbody></table>`);

        const q = data.queue || {};
        this.wrapper.find(".tb-queue-grid").html(Object.entries(q).map(([k,v]) => `<div class="tb-queue-stat"><span>${k}</span><b>${v}</b></div>`).join("") || "<span>No queue jobs.</span>");
        const create = s.create_required || 0;
        this.wrapper.find(".tb-provision-controls").html(`<div class="tb-provision-row">
            <div><label>Batch size</label><input class="form-control tb-limit" type="number" min="1" max="1000" value="${Math.min(create || 100, 1000)}"></div>
            <div><label>Scope</label><select class="form-control tb-scope"><option value="UOM,Account,Customer,Supplier,Item">All supported masters</option><option value="UOM">UOM only</option><option value="Account">Accounts only</option><option value="Customer,Supplier">Customers + Suppliers</option><option value="Item">Items only</option></select></div>
            <div class="tb-action"><button class="btn btn-primary tb-provision">Queue Create Required</button></div>
        </div><div class="text-muted">Jobs are dependency-aware and delivered by the Windows Agent.</div>`);
        this.wrapper.find(".tb-provision").on("click", () => this.provision());
    }

    async reconcile() {
        if (!this.connection) return frappe.msgprint(__("Select a Tally connection first."));
        frappe.show_alert({message: __("Reconciliation queued"), indicator: "blue"});
        await this.call("reconcile_masters", {connection: this.connection});
        setTimeout(() => this.refresh(), 1200);
    }

    async provision() {
        if (!this.connection) return;
        const limit = Math.max(1, Math.min(1000, Number(this.wrapper.find(".tb-limit").val()) || 100));
        const scope = this.wrapper.find(".tb-scope").val();
        frappe.confirm(__(`Queue up to ${limit} Create Required masters?`), async () => {
            const r = await this.call("provision_reconciled_masters", {connection:this.connection, source_doctypes:scope, limit:limit});
            this.wrapper.find(".tb-provision-result").html(`Queued <b>${r.queued || 0}</b>; skipped <b>${r.skipped || 0}</b>.`);
            frappe.show_alert({message: __(`${r.queued || 0} masters queued`), indicator: "green"});
            setTimeout(() => this.refresh(), 800);
        });
    }

    async provision_all() {
        if (!this.connection) return;
        frappe.confirm(__("Queue all currently Create Required masters? This creates sync jobs; the Agent sends them to TallyPrime."), async () => {
            const r = await this.call("provision_reconciled_masters", {connection:this.connection, source_doctypes:"UOM,Account,Customer,Supplier,Item", limit:1000});
            frappe.msgprint({title:__("Master Provisioning"), message:__(`Queued <b>${r.queued || 0}</b> masters. The Windows Agent will process them.`), indicator:"green"});
            setTimeout(() => this.refresh(), 1000);
        });
    }

    add_styles() {
        if (document.getElementById("tally-bridge-console-css")) return;
        const style = document.createElement("style"); style.id = "tally-bridge-console-css";
        style.textContent = `
            .tb-console{padding:8px 4px 32px}.tb-toolbar{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:18px}.tb-connection{width:420px}
            .tb-connection label,.tb-provision-row label{font-size:12px;color:var(--text-muted);display:block;margin-bottom:5px}
            .tb-cards{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:12px;margin-bottom:16px}.tb-card,.tb-panel{border:1px solid var(--border-color);border-radius:10px;background:var(--card-bg)}
            .tb-card{padding:16px;min-height:86px}.tb-card span{display:block;color:var(--text-muted);font-size:12px}.tb-card strong{display:block;font-size:27px;margin-top:8px}.tb-card.ok strong{color:var(--green-500)}.tb-card.warn strong{color:var(--orange-500)}.tb-card.danger strong,.tb-danger{color:var(--red-500)}
            .tb-grid{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:16px}.tb-panel{padding:18px;margin-bottom:16px}.tb-panel-head{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:16px}.tb-panel-head h4{margin:0 0 3px}.tb-panel-head p{margin:0;color:var(--text-muted);font-size:12px}
            .tb-inline{display:flex;gap:24px;flex-wrap:wrap;padding:10px 0 16px}.tb-inline span{color:var(--text-muted)}.tb-inline b{color:var(--text-color);margin-left:4px}
            .tb-provision-row{display:grid;grid-template-columns:120px 1fr auto;gap:12px;align-items:end;margin-bottom:10px}.tb-queue-grid{display:grid;grid-template-columns:repeat(6,minmax(100px,1fr));gap:10px}.tb-queue-stat{padding:12px;border:1px solid var(--border-color);border-radius:8px}.tb-queue-stat span{display:block;color:var(--text-muted);font-size:12px}.tb-queue-stat b{font-size:20px}
            @media(max-width:1000px){.tb-cards{grid-template-columns:repeat(3,1fr)}.tb-grid{grid-template-columns:1fr}.tb-queue-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.tb-cards{grid-template-columns:repeat(2,1fr)}.tb-toolbar,.tb-provision-row{display:block}.tb-connection{width:100%;margin-bottom:12px}.tb-queue-grid{grid-template-columns:repeat(2,1fr)}}
        `;
        document.head.appendChild(style);
    }
}
