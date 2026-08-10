frappe.pages["ai-chat"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI"),
		single_column: true,
	});

	const container = $('<div class="erpnext-ai-root"></div>').appendTo(
		$(wrapper).find(".layout-main-section")
	)[0];

	// Cache-bust by hand. frappe.assets.execute() appends `?v=` to everything EXCEPT
	// paths containing ".bundle." -- it assumes frappe's own esbuild content-hashed
	// those, which is true for its bundles and false for this one: Vite writes it to
	// a fixed path. Without a version the proxy's Cache-Control (hours) pins whatever
	// the browser saw first, so a deploy keeps serving the previous UI -- and a 404
	// cached while the container was restarting keeps "bundle failed to load" alive
	// just as long, with a perfectly good file sitting on the server.
	const version = window._version_number || "";
	const bundle = `/assets/erpnext/ai/ai.bundle.js?v=${encodeURIComponent(version)}`;

	// The SPA is built as an IIFE exposing window.mountAI so it can live inside the
	// desk shell — which is the whole point, since the AI entry is a rail icon and
	// navigating away from the desk would lose the rail.
	frappe.require(bundle, () => {
		if (window.mountAI) {
			window.mountAI(container);
		} else {
			// frappe.require resolves on error too, so this branch is also what a
			// failed request looks like -- say so, instead of blaming the build.
			container.innerText = __(
				"AI bundle did not load from {0}. Reload with cache bypass (Ctrl/Cmd+Shift+R); if it persists, run: yarn build:ai",
				[bundle]
			);
		}
	});
};
