(function(define) {
    'use strict'; 
    RequireJS.define("js/student_account/logistration_factory", ["jquery", "js/student_account/views/AccessView"], function(e, t) {
        return function(r) {
            var i = e("#login-and-registration-container");
            new t(_.extend(r, {
                el: i
            }))
        }
    })
}).call(this, define || RequireJS.define);
