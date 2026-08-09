frappe.pages["ai"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI"),
		single_column: true,
	});

	const container = $('<div class="erpnext-ai-root"></div>').appendTo(
		$(wrapper).find(".layout-main-section")
	)[0];

	// The SPA is built as an IIFE exposing window.mountAI so it can live inside the
	// desk shell — which is the whole point, since the AI entry is a rail icon and
	// navigating away from the desk would lose the rail.
	frappe.require("/assets/erpnext/ai/ai.bundle.js", () => {
		if (window.mountAI) {
			window.mountAI(container);
		} else {
			container.innerText = __("AI bundle failed to load. Run: yarn build:ai");
		}
	});
};
