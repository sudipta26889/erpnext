frappe.pages["ai-chat"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI"),
		single_column: true,
	});

	// page.body is frappe.ui.Page's own handle on .layout-main-section (see
	// ui/page.js::setup_page). Re-querying the DOM for that class instead would
	// silently yield an empty jQuery set the day the desk shell changes -- and
	// appendTo() on an empty set still hands back the detached div, so React
	// would mount into a node that is not in the document and render nothing,
	// with no error anywhere. Fall back to the wrapper rather than mount blind.
	const host = page.body?.length ? page.body : $(wrapper);
	const container = $('<div class="erpnext-ai-root"></div>').appendTo(host)[0];

	const bundle = "/assets/erpnext/ai/ai.js";

	// The SPA is built as an IIFE exposing window.mountAI so it can live inside the
	// desk shell — which is the whole point, since the AI entry is a rail icon and
	// navigating away from the desk would lose the rail.
	frappe.require(bundle, () => {
		if (!window.mountAI) {
			// frappe.require resolves on error too, so this branch is also what a
			// failed request looks like -- say so, instead of blaming the build.
			container.innerText = __(
				"AI bundle did not load from {0}. Reload with cache bypass (Ctrl/Cmd+Shift+R); if it persists, run: yarn build:ai",
				[bundle]
			);
			return;
		}
		try {
			window.mountAI(container);
		} catch (e) {
			// A throw inside mount leaves an empty page and an empty console line in
			// production React. Put it on screen; a blank tab is not a bug report.
			console.error("erpnext-ai mount failed", e);
			container.innerText = __("AI failed to start: {0}", [e.message || String(e)]);
		}
		if (!document.body.contains(container)) {
			console.error("erpnext-ai: mount target is not in the document", { host, container });
		}
	});
};
