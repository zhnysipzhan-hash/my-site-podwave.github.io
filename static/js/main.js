// PodWave — интерактивті элементтер

document.addEventListener('DOMContentLoaded', function () {
    // Flash хабарламаларды 5 секундтан кейін жасыру
    document.querySelectorAll('.alert-dismissible').forEach(function (alert) {
        setTimeout(function () {
            var bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            bsAlert.close();
        }, 5000);
    });

    // iTunes trending AJAX жүктеу (fallback)
    var fallback = document.getElementById('trending-fallback');
    if (fallback) {
        fetch('/api/trending')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.length === 0) return;
                var container = document.getElementById('trending-container');
                container.innerHTML = '';
                data.forEach(function (item) {
                    var col = document.createElement('div');
                    col.className = 'col-md-6 col-lg-3';
                    col.innerHTML =
                        '<div class="card h-100 shadow-sm border-0 trending-card">' +
                        (item.image
                            ? '<img src="' + item.image + '" class="card-img-top trending-img" alt="">'
                            : '<div class="card-img-top podcast-cover d-flex align-items-center justify-content-center" style="height:160px"><i class="bi bi-broadcast display-4 text-white"></i></div>') +
                        '<div class="card-body"><h6 class="card-title">' + item.title + '</h6>' +
                        '<p class="card-text small text-muted">' + item.artist + '</p>' +
                        '<span class="badge bg-warning text-dark">' + item.genre + '</span></div>' +
                        '<div class="card-footer bg-transparent border-0 pb-3">' +
                        '<a href="' + item.url + '" target="_blank" rel="noopener" class="btn btn-outline-primary btn-sm w-100">' +
                        '<i class="bi bi-box-arrow-up-right"></i> iTunes-та ашу</a></div></div>';
                    container.appendChild(col);
                });
            })
            .catch(function () { /* желі қатесі — fallback қалады */ });
    }

    // Аудио плеер прогрессін сақтау
    var player = document.getElementById('player');
    if (player) {
        var key = 'podwave_pos_' + window.location.pathname;
        player.addEventListener('loadedmetadata', function () {
            var saved = localStorage.getItem(key);
            if (saved) player.currentTime = parseFloat(saved);
        });
        player.addEventListener('timeupdate', function () {
            localStorage.setItem(key, player.currentTime);
        });
    }

    // Іздеу формасында Enter басу
    document.querySelectorAll('input[name="q"]').forEach(function (input) {
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.target.closest('form')?.submit();
            }
        });
    });
});
