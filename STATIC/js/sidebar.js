// static/js/sidebar.js
document.addEventListener('DOMContentLoaded', () => {
  const toggleSidebar = document.getElementById('toggleSidebar');
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('overlay');
  let isOpen = false;

  function openSidebar() {
    sidebar.style.right = '0';
    overlay.style.display = 'block';
    isOpen = true;
  }

  function closeSidebar() {
    sidebar.style.right = '-250px';
    overlay.style.display = 'none';
    isOpen = false;
  }

  if (!toggleSidebar || !sidebar || !overlay) return;

  toggleSidebar.addEventListener('click', (e) => {
    e.stopPropagation();
    isOpen ? closeSidebar() : openSidebar();
  });

  overlay.addEventListener('click', closeSidebar);
});
