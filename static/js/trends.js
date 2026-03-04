// Trends page interactions
document.addEventListener('DOMContentLoaded', function () {
    // Auto-dismiss flash messages
    const flashes = document.querySelectorAll('.flash');
    flashes.forEach(function (flash) {
        setTimeout(function () {
            flash.style.opacity = '0';
            setTimeout(function () { flash.remove(); }, 300);
        }, 5000);
    });
});
