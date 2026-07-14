/* Helpers certificat IUN Senegal - servi par le mock countryconfig (cle CM handlebars.js). */
/* Reprend les 4 helpers Farajaland + frLongDate (mois en francais pour certificateDate). */
/* IMPORTANT : pas de commentaire de fin de ligne dans ce fichier (survit a une perte de retours a la ligne). */
export function noop(props) { return function (value) { return value; }; }
export function debug() { return function (value) { console.log(this); return value; }; }
export function ordinalFormatDate() {
  return function (dateString) {
    var date = new Date(String(dateString).trim());
    var day = date.getDate();
    var suffix = "th";
    if (day <= 3 || day >= 21) {
      if (day % 10 === 1) { suffix = "st"; }
      else if (day % 10 === 2) { suffix = "nd"; }
      else if (day % 10 === 3) { suffix = "rd"; }
    }
    var month = date.toLocaleString("default", { month: "long" });
    return day + suffix + " " + month + " " + date.getFullYear();
  };
}
export function getCamelCasedInformantType(props) {
  return function (informantType, otherInformantType) {
    var camelCased = String(informantType).toLowerCase().split("_").map(function (w, i) {
      return i === 0 ? w : w.charAt(0).toUpperCase() + w.slice(1);
    }).join("");
    return props.intl.formatMessage(
      { id: "form.field.label.informantRelation." + camelCased, description: "Label for informant type", defaultMessage: "" },
      { otherInformantType: otherInformantType }
    );
  };
}
export function frLongDate() {
  return function (value) {
    if (!value) { return value; }
    var m = {
      January: "janvier", February: "f\u00e9vrier", March: "mars", April: "avril",
      May: "mai", June: "juin", July: "juillet", August: "ao\u00fbt",
      September: "septembre", October: "octobre", November: "novembre", December: "d\u00e9cembre"
    };
    return String(value).replace(/January|February|March|April|May|June|July|August|September|October|November|December/g, function (k) { return m[k]; });
  };
}
