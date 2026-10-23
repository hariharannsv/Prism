"""URL configuration for the core app."""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from core.fanuc.dashboard import (
    fanuc_api_dashboard,
    fanuc_api_machines,
    fanuc_dashboard_page,
)
from core.oee_report_source import oee_report_source as oee_report_source_view
from core.views import (
    addmachine,
    asspack,
    cell,
    cellmaster,
    dailyplan,
    demandmaster,
    devicestatus,
    downtime,
    errors,
    finalassembly,
    heattreatment,
    heattreatment_traceability,
    home,
    hourlyreport,
    machinecost,
    machinedashboard,
    mainviews,
    oeedailyreport,
    oeereport,
    packing,
    partcost,
    partno,
    planning,
    powerconsumption,
    prepacking,
    project,
    qualityfeeder,
    reportdashboard,
    revtracereport,
    role,
    setup_inspection,
    toolmanagement,
    wip_report,
    wipdashboard,
    wipreport,
)
from core.views.downtime import (
    Planneddowntime,
    Unplanneddowtime,
    add_unplanned_downtime,
    get_unplanned_downtime_data,
)
from core.views.Email import (
    email_config_detail,
    email_config_rows,
    email_config_save,
    email_page,
)
from core.views.furnace import (
    charge_report,
    cmbaf,
    cycle_time_analysis,
    dashboard,
    furnace_part_spec,
    furnace_report,
    furnace_scada_params,
    furnace_trend_dashboard,
    loading_time_analysis,
    utilization_analysis,
)
from core.views.line_master import (
    line_master_delete_api,
    line_master_list_api,
    line_master_page,
    line_master_save_api,
    line_master_update_api,
)
from core.views.line_monitoring import (
    line_monitoring,
    line_monitoring_dashboard,
    line_monitoring_dashboard_api,
    line_monitoring_project_select,
)
from core.views.module_settings import (
    module_settings_list,
    module_settings_page,
    module_settings_toggle,
)
from core.views.pressmachine import (
    combined_dashboard as pressmachine_combined_dashboard,
)
from core.views.pressmachine import daily_dashboard as pressmachine_daily_dashboard
from core.views.pressmachine import dashboard as pressmachine_dashboard
from core.views.pressmachine import downtime_reports as pressmachine_downtime_reports
from core.views.pressmachine import firststage as pressmachine_firststage
from core.views.pressmachine import machine_board as pressmachine_machine_board
from core.views.pressmachine import report_dashboard as pressmachine_report_dashboard
from core.views.pressmachine import reports as pressmachine_reports
from core.views.pressmachine import workorder as pressmachine_workorder
from core.views.pressmachine import (
    workorder_schedule as pressmachine_workorder_schedule,
)
from core.views.pressmachine.production_dashboard import (
    production_status_dashboard_api,
    production_status_dashboard_page,
)
from core.views.role_module_settings import (
    role_module_settings_list,
    role_module_settings_page,
    role_module_settings_toggle,
)
from core.views.shift_settings import (
    shift_settings_delete,
    shift_settings_list,
    shift_settings_page,
    shift_settings_save,
)
from core.views.tool_calibration import calibration_views
from core.views.tool_calibration import dashboard as tool_calib_dashboard
from core.views.tool_calibration import fixture_master, masters, tool_pages

extraUrls = [
    # path("fileimport", mainviews.fileimport, name="fileimport"),
]

Dashboard_OEEproject = [
    path("", mainviews.SignIn, name="signin"),
    path("auth/", mainviews.Authpage, name="auth"),
    path("logout", mainviews.SignOut, name="logout"),
    path("__error__/500/", errors.preview_server_error, name="preview_server_error"),
    path(
        "project_select/",
        machinedashboard.machine_dashboard_project_select,
        name="project_select",
    ),
    path(
        "machine_dashboard/project_select/",
        machinedashboard.machine_dashboard_project_select,
        name="machine_dashboard_project_select",
    ),
    path(
        "machine_dashboard/",
        machinedashboard.machine_dashboard,
        name="machine_dashboard",
    ),
    path(
        "machine_dashboard/api/kpi/",
        machinedashboard.api_dashboard_kpi,
        name="api_dashboard_kpi",
    ),
    path(
        "machine_dashboard/api/chart/oee/",
        machinedashboard.api_dashboard_chart_oee,
        name="api_dashboard_chart_oee",
    ),
    path(
        "machine_dashboard/api/chart/runtime-downtime/",
        machinedashboard.api_dashboard_chart_runtime_downtime,
        name="api_dashboard_chart_runtime_downtime",
    ),
    path(
        "machine_dashboard/api/chart/rejection/",
        machinedashboard.api_dashboard_chart_rejection,
        name="api_dashboard_chart_rejection",
    ),
    path(
        "machine_dashboard/api/chart/counts/",
        machinedashboard.api_dashboard_chart_counts,
        name="api_dashboard_chart_counts",
    ),
    path(
        "machine_dashboard/api/chart/downtime-hours/",
        machinedashboard.api_dashboard_chart_downtime_hours,
        name="api_dashboard_chart_downtime_hours",
    ),
    path(
        "machine_dashboard/api/stroke/",
        machinedashboard.api_dashboard_stroke,
        name="api_dashboard_stroke",
    ),
    path(
        "machine_dashboard/api/planned-actual/",
        machinedashboard.api_dashboard_planned_actual,
        name="api_dashboard_planned_actual",
    ),
    path(
        "machine_dashboard/api/unplanned-reasons/",
        machinedashboard.api_dashboard_unplanned_reasons,
        name="api_dashboard_unplanned_reasons",
    ),
    path(
        "machine_dashboard/hourly-report/",
        hourlyreport.hourly_report_page,
        name="hourly_report_page",
    ),
    path(
        "machine_dashboard/api/hourly-report/",
        hourlyreport.hourly_report_api,
        name="hourly_report_api",
    ),
    path(
        "furnace/trend-dashboard/",
        furnace_trend_dashboard.furnace_trend_dashboard_page,
        name="furnace_trend_dashboard",
    ),
    path(
        "furnace/trend-dashboard/data/",
        furnace_trend_dashboard.furnace_trend_dashboard_data,
        name="furnace_trend_dashboard_data",
    ),
    path(
        "furnace/ajax/charge-nos/",
        furnace_trend_dashboard.furnace_trend_charge_nos,
        name="furnace_trend_charge_nos",
    ),
    path("project/", project.ProjectCreation, name="project"),
    path("customer_select/", project.customer_select, name="customer_select"),
    path(
        "set_selected_customer/",
        project.set_selected_customer,
        name="set_selected_customer",
    ),
    path("projectlist/", project.Project_list, name="projectlist"),
    path("add_partno/<str:project_id>/", partno.add_partno, name="add_partno"),
    path("partnos/<str:project_id>/", partno.partno_list, name="partno_list"),
    path("add_process/<str:partno_id>/", cell.add_process, name="add_cell_process"),
    path("station/<str:partno_id>/", cell.Station, name="station"),
    path("editstation/<str:partno_id>/", cell.editstation, name="editstation"),
    path(
        "update_process/<str:partno_id>/",
        cell.update_process,
        name="update_cell_process",
    ),
    # path("celllist/<str:cell_id>/", cell.Celllist, name="celllist"),
    path("get_machine_names/", cell.machine_dropdown, name="get_machine_names"),
    path(
        "get_machine_status/<str:machine_name>/",
        cell.machine_status,
        name="get_machine_status",
    ),
    path("plannedlist/", downtime.Plannedlist, name="plannedlist"),
    path("planned_downtime", downtime.Planneddowntime, name="planned_downtime"),
    path(
        "add_planned_downtime/",
        downtime.add_planned_downtime,
        name="add_planned_downtime",
    ),
    path("unplanned_downtime", downtime.Unplanneddowtime, name="unplanned_downtime"),
    path(
        "add-unplanned-downtime/",
        downtime.add_unplanned_downtime,
        name="add_unplanned_downtime",
    ),
    path("unplannedlist/", downtime.Unplannedlist, name="unplannedlist"),
    path("get_short_text/", downtime.get_short_text, name="get_short_text"),
    # path("downtimelist/", downtime.TotalDowntime, name="downtimelist"),
    path("add_cost/", machinecost.add_cost, name="add_cost"),
    path(
        "machinecostreport/", machinecost.machinecost_report, name="machinecostreport"
    ),
    path(
        "calculate_running_hours/",
        machinecost.calculate_running_hours,
        name="calculate_running_hours",
    ),
    path("dailyplan/", dailyplan.Dailyplanproduction, name="dailyplan"),
    path("daily_planlist/", dailyplan.daily_planlist, name="daily_planlist"),
    path("policy/", dailyplan.policy, name="policy"),
    path("add_machine/", addmachine.add_machine, name="add_machine"),
    path("machinelist/", addmachine.Machinelist, name="machinelist"),
    path(
        "edit-machine-category/",
        addmachine.edit_machine_category,
        name="edit_machine_category",
    ),
    path("edit-machine/", addmachine.edit_machine, name="edit_machine"),
    path(
        "update-machine-line/",
        addmachine.update_machine_line,
        name="update_machine_line",
    ),
    path("machine-enabled/", addmachine.toggle_machine_status, name="machine_status"),
    path(
        "update-plan-status/", dailyplan.update_plan_status, name="update_plan_status"
    ),
    path("addpartcost/", partcost.add_part_cost, name="addpartcost"),
    path("partcostreport/", partcost.partcost_report, name="partcostreport"),
    path("createrole/", role.createrole, name="createrole"),
    path("assignrole/", role.assignrole, name="assignrole"),
    path("get-role-name/", role.get_role_name, name="get_role_name"),
    path("wipdashboard/", wipdashboard.wipdasboard, name="wipdashboard"),
    path("wip_report/", wip_report.wip_report, name="wip_report"),
    path("wip_report_detail/", wip_report.wip_report_detail, name="wip_report_detail"),
    path("logs/", mainviews.view_logs, name="view_logs"),
    path("get-processes/", dailyplan.get_processes_by_part, name="get_processes"),
    path("get-machines/", dailyplan.get_machines_by_process, name="get_machines"),
    path("qualityfeeder/", qualityfeeder.quality_feeder, name="qualityfeeder"),
    path(
        "qualityfeeder/filter/",
        qualityfeeder.qualityfeeder_filter,
        name="qualityfeeder_filter",
    ),
    path(
        "qualityfeeder/approve/",
        qualityfeeder.approve_workorder,
        name="approve_workorder",
    ),
    path(
        "qualityfeeder/lookups/",
        qualityfeeder.qualityfeeder_lookups,
        name="qualityfeeder_lookups",
    ),
    path(
        "setup-inspection-report/",
        setup_inspection.setup_inspection_report,
        name="setup_inspection_report",
    ),
    path(
        "setup-inspection/filter/",
        setup_inspection.setup_inspection_filter,
        name="setup_inspection_filter",
    ),
    path(
        "approve-setup-workorder/",
        setup_inspection.approve_setup_workorder,
        name="approve_setup_workorder",
    ),
    path("wipreport/", wipreport.wipreport, name="wipreport"),
    path("firststage/", wipreport.firststagereport, name="firststage"),
    path(
        "wipreport_filter/filter/",
        wipreport.wipreport_filter,
        name="wipreport_filter",
    ),
    path(
        "firststage/filter/",
        wipreport.firststage_report_filter,
        name="firststage_report_filter",
    ),
    path("parttrace/", wipreport.parttrace, name="parttrace"),
    path("firststage/", wipreport.firststagereport, name="firststage"),
    path(
        "wipreport_filter/filter/",
        wipreport.wipreport_filter,
        name="wipreport_filter",
    ),
    path(
        "firststage/filter/",
        wipreport.firststage_report_filter,
        name="firststage_report_filter",
    ),
    path(
        "get_machines_by_partname/",
        dailyplan.get_machines_by_partname,
        name="get_machines_by_partname",
    ),
    path("home/", home.plant_data_screen, name="plant_data_screen"),
    path("get_plant_data/", home.get_plant_data, name="get_plant_data"),
    path("set_plant_code/", home.set_plant_code, name="set_plant_code"),
    path(
        "furnace/utilization/",
        utilization_analysis.utilization_analysis_view,
        name="furnace_utilization",
    ),
    path(
        "furnace/energy/",
        dashboard.energy_monitoring,
        name="furnace_energy",
    ),
    path(
        "furnace/charge/",
        dashboard.charge_analysis,
        name="furnace_charge",
    ),
    path(
        "furnace/charge/<str:machine>/<str:charge_no>/report/",
        charge_report.charge_report,
        name="furnace_charge_report",
    ),
    path(
        "furnace/ajax/charge-report/",
        charge_report.charge_report_data,
        name="charge_report_data",
    ),
    path(
        "furnace/loading-time/",
        loading_time_analysis.loading_time_analysis,
        name="furnace_loading",
    ),
    path(
        "furnace/cycle-time/",
        cycle_time_analysis.cycle_time_analysis,
        name="furnace_cycle",
    ),
    path(
        "furnace/ajax/utilization/",
        utilization_analysis.furnace_utilization_data,
        name="furnace_utilization_data",
    ),
    path(
        "furnace/ajax/energy/",
        dashboard.furnace_energy_data,
        name="furnace_energy_data",
    ),
    path(
        "furnace/ajax/charge/",
        dashboard.furnace_charge_data,
        name="furnace_charge_data",
    ),
    path(
        "furnace/ajax/charge-report/excel/",
        dashboard.furnace_charge_report_excel,
        name="furnace_charge_report_excel",
    ),
    path(
        "furnace/ajax/loading/",
        loading_time_analysis.furnace_loading_data,
        name="furnace_loading_data",
    ),
    path(
        "furnace/ajax/cycle/",
        cycle_time_analysis.furnace_cycle_data,
        name="furnace_cycle_data",
    ),
    path(
        "furnace-dashboard/",
        dashboard.furnace_dashboard,
        name="furnace_dashboard",
    ),
    path(
        "furnace-dashboard/api/data/",
        dashboard.furnace_dashboard_data,
        name="furnace_dashboard_data",
    ),
    path(
        "fanuc/dashboard/",
        fanuc_dashboard_page,
        name="fanuc_dashboard",
    ),
    path(
        "fanuc/api/dashboard/",
        fanuc_api_dashboard,
        name="fanuc_api_dashboard",
    ),
    path(
        "fanuc/api/machines/",
        fanuc_api_machines,
        name="fanuc_api_machines",
    ),
    path("cmbaf/", cmbaf.cmbaf_dashboard, name="cmbaf_dashboard"),
    path("cmbaf/api/data/", cmbaf.cmbaf_data, name="cmbaf_data"),
    path(
        "furnace/data-logger/",
        furnace_report.data_logger_report_page,
        name="furnace_data_logger_report",
    ),
    path(
        "furnace/ajax/data-logger/",
        furnace_report.data_logger_report_query,
        name="furnace_data_logger_query",
    ),
    path(
        "furnace/data-logger/export/excel/",
        furnace_report.data_logger_report_export_excel,
        name="furnace_data_logger_export_excel",
    ),
    path(
        "furnace/data-logger/export/pdf/",
        furnace_report.data_logger_report_export_pdf,
        name="furnace_data_logger_export_pdf",
    ),
    path(
        "furnace/part-master/",
        furnace_part_spec.furnace_part_spec_page,
        name="furnace_part_spec",
    ),
    path(
        "furnace/ajax/part-master/list/",
        furnace_part_spec.furnace_part_spec_list,
        name="furnace_part_spec_list",
    ),
    path(
        "furnace/ajax/part-master/save/",
        furnace_part_spec.furnace_part_spec_save,
        name="furnace_part_spec_save",
    ),
    path(
        "furnace/ajax/part-master/delete/",
        furnace_part_spec.furnace_part_spec_delete,
        name="furnace_part_spec_delete",
    ),
    path(
        "furnace/scada-params/",
        furnace_scada_params.furnace_scada_params_page,
        name="furnace_scada_params",
    ),
    path(
        "furnace/ajax/scada-params/list/",
        furnace_scada_params.furnace_scada_params_list,
        name="furnace_scada_params_list",
    ),
    path(
        "furnace/ajax/scada-params/save/",
        furnace_scada_params.furnace_scada_params_save,
        name="furnace_scada_params_save",
    ),
    path(
        "furnace/ajax/scada-params/validate/",
        furnace_scada_params.furnace_scada_params_validate,
        name="furnace_scada_params_validate",
    ),
    path(
        "furnace/ajax/scada-params/alert-history/",
        furnace_scada_params.furnace_scada_alert_history,
        name="furnace_scada_alert_history",
    ),
    path("heat_treatment/", heattreatment.heattreatment, name="heat_treatment"),
    path(
        "heat_treatment/traceability/",
        heattreatment_traceability.heat_treatment_traceability_page,
        name="heat_treatment_traceability",
    ),
    path(
        "heat_treatment/traceability/data/",
        heattreatment_traceability.heat_treatment_traceability_data,
        name="heat_treatment_traceability_data",
    ),
    path(
        "heat_treatment/traceability/history-card/",
        heattreatment_traceability.heat_treatment_traceability_history_card,
        name="heat_treatment_traceability_history_card",
    ),
    path(
        "heat_treatment/report/filter/",
        heattreatment.heat_treatment_report_filter,
        name="heat_treatment_report_filter",
    ),
    path(
        "heat_treatment/report/charge-nos/",
        heattreatment.heat_treatment_charge_nos,
        name="heat_treatment_charge_nos",
    ),
    path(
        "heat_treatment/add/", heattreatment.add_heattreatment, name="add_heattreatment"
    ),
    path(
        "listheattreatment/",
        heattreatment.list_heattreatment,
        name="listheattreatment",
    ),
    # urls.py
    path(
        "getProcessMachineDataURL/",
        cell.get_process_machine_data,
        name="getProcessMachineDataURL",
    ),
    path("oeereport/", oeereport.oeereport, name="oeereport"),
    path("oeedailyreport/", oeedailyreport.oeedailyreport, name="oeedailyreport"),
    path(
        "oee_report_source/",
        oee_report_source_view,
        name="oee_report_source",
    ),
    path("dbnotconned/", mainviews.Dbnotconned, name="dbnotconned"),
    path("reportdashboard/", reportdashboard.reportdashboard, name="reportdashboard"),
    path(
        "get-sap-workcenters/",
        addmachine.get_sap_workcenters,
        name="get_sap_workcenters",
    ),
    path("cellmaster/", cellmaster.cellmaster, name="cellmaster"),
    path("get_cell_name/", cellmaster.get_cell_name, name="get_cell_name"),
    path("planneddowntime/", Planneddowntime, name="planneddowntime"),
    path("unplanneddowntime/", Unplanneddowtime, name="unplanneddowntime"),
    path(
        "add_unplanned_downtime/",
        add_unplanned_downtime,
        name="add_unplanned_downtime_alt",
    ),
    path(
        "heattreatmentreport/",
        heattreatment.heat_treatment_report,
        name="heattreatmentreport",
    ),
    path(
        "get_unplanned_downtime_data/",
        get_unplanned_downtime_data,
        name="get_unplanned_downtime_data",
    ),
    path(
        "power_consumption/",
        powerconsumption.power_consumption,
        name="power_consumption",
    ),
    path(
        "power/ajax/machine/",
        powerconsumption.power_ajax_machine,
        name="power_ajax_machine",
    ),
    path(
        "power/ajax/hourly/",
        powerconsumption.power_ajax_hourly,
        name="power_ajax_hourly",
    ),
    path(
        "power/ajax/daily/", powerconsumption.power_ajax_daily, name="power_ajax_daily"
    ),
    path("power/live/", powerconsumption.power_ajax_seconds, name="power_ajax_seconds"),
    path("kpi-data/", powerconsumption.power_kpi_data, name="power_kpi_data"),
    path(
        "assemblyreport/filter/",
        asspack.assemblyreport_filter,
        name="assemblyreport_filter",
    ),
    path(
        "prepackingreport_filter/filter/",
        asspack.prepackingreport_filter,
        name="prepackingreport_filter",
    ),
    path(
        "prepackingreport_filter/options/",
        asspack.prepackingreport_filter_options,
        name="prepackingreport_filter_options",
    ),
    path(
        "prepackingreport_pdf/",
        asspack.prepacking_report_pdf,
        name="prepackingreport_pdf",
    ),
    path(
        "asspack/",
        asspack.asspack,
        name="asspack",
    ),
    path(
        "packing_report_filter/filter/",
        asspack.packing_report_filter,
        name="packing_report_filter",
    ),
    path("reprint_packing/", packing.reprint_for_packing, name="packing_print"),
    path(
        "packing_report_excel/",
        asspack.packing_report_excel,
        name="packing_report_excel",
    ),
    path("demandmaster/", demandmaster.demand_master, name="demandmaster"),
    path(
        "packing_report_page/", asspack.packing_report_page, name="packing_report_page"
    ),
    path(
        "get_demand_master_data/",
        demandmaster.get_demand_master_data,
        name="get_demand_master_data",
    ),
    path("devices/", devicestatus.DeviceList, name="devices"),
    path("devices/add/", devicestatus.add_device, name="add_device"),
    path(
        "save_demand_master_data/",
        demandmaster.save_demand_master_data,
        name="save_demand_master_data",
    ),
    path("revtrace/", revtracereport.reverse_traceability_view, name="revtrace"),
    path("ppcdemand/", demandmaster.ppcdemand, name="ppcdemand"),
    path("rmdemand/", demandmaster.rmdemand, name="rmdemand"),
    path(
        "ppc-blankreq/<str:record_id>/", demandmaster.ppc_blankreq, name="ppc_blankreq"
    ),
    path("packing", packing.packing, name="packing"),
    path("add_packing/", packing.add_packing, name="add_packing"),
    path("prepacking_list/", prepacking.prepacking_list, name="prepackinglist"),
    path(
        "view_prepacking/<str:project_id>/",
        prepacking.view_prepacking_process,
        name="view_prepacking",
    ),
    path("packing_list/", packing.packing_list, name="packing_list"),
    path(
        "packing/view/<str:project_id>/",
        packing.view_packing_process,
        name="view_packing_process",
    ),
    path("pre_packing", prepacking.pre_packing, name="pre_packing"),
    path(
        "get_prepacking_processes/",
        prepacking.get_prepacking_processes,
        name="get_prepacking_processes",
    ),
    path(
        "save_prepacking_sequence/",
        prepacking.add_prepacking_sequence,
        name="save_prepacking_sequence",
    ),
    path("subprocesses/", finalassembly.sub_process_list, name="sub_process_list"),
    path(
        "subprocess/edit/<str:project_id>/", finalassembly.processseq, name="processseq"
    ),
    path(
        "subprocess/view/<str:project_id>/",
        finalassembly.view_process,
        name="edit_process",
    ),
    path(
        "subprocess/delete/<str:project_id>/",
        finalassembly.delete_process,
        name="delete_process",
    ),
    path(
        "traceability_logs",
        finalassembly.view_logs,
        name="traceability_logs",
    ),
    path("add_process", finalassembly.add_process, name="add_process"),
    # path("main_processlist/", finalassembly.Process_list, name="main_processlist"),
    path("processseq/<str:project_id>/", finalassembly.processseq, name="processseq"),
    path("addprocesslist/", finalassembly.add_process_list, name="addprocesslist"),
    path(
        "add_process_sequence",
        finalassembly.add_process_sequence,
        name="add_process_sequence",
    ),
    path(
        "get_sap_data_subprocess/<str:object_id>/",
        finalassembly.get_sap_data_subprocess,
        name="get_sap_data_subprocess",
    ),
    path(
        "master/tool-fixture/add/",
        fixture_master.tool_fixture_master_add,
        name="tool_fixture_master_add",
    ),
    path(
        "master/tool-fixture/list/",
        fixture_master.tool_fixture_master_list,
        name="tool_fixture_master_list",
    ),
    path(
        "master/tool-entry/<tool_number>/",
        tool_pages.tool_entry_page,
        name="tool_entry_page",
    ),
    path(
        "master/tool-history/<tool_number>/",
        tool_pages.tool_history_page,
        name="tool_history_page",
    ),
    path(
        "master/tool-calibrate/<str:tool_number>/",
        tool_pages.tool_calibration_page,
        name="tool_calibration_page",
    ),
    path("master/service/", masters.service_master, name="service_master"),
    path("master/comments/", masters.comments_master, name="comments_master"),
    path("master/service/update/", masters.update_service, name="update_service"),
    path("master/service/delete/", masters.delete_service, name="delete_service"),
    path(
        "master/comments/update/",
        masters.update_comment,
        name="update_comment",
    ),
    path(
        "master/comments/delete/",
        masters.delete_comment,
        name="delete_comment",
    ),
    # calibration list pages (GLOBAL)
    path(
        "master/tool-calibration/pass/",
        calibration_views.calibration_list_by_status,
        {"status": "Pass"},
        name="pass_calibrations",
    ),
    path(
        "master/tool-calibration/service/",
        calibration_views.calibration_list_by_status,
        {"status": "Service"},
        name="service_calibrations",
    ),
    path(
        "master/tool-calibration/rejection/",
        calibration_views.calibration_list_by_status,
        {"status": "Rejection"},
        name="reject_calibrations",
    ),
    path(
        "master/vendors/",
        masters.vendor_master,
        name="vendor_master",
    ),
    path(
        "master/vendors/update/",
        masters.update_vendor,
        name="update_vendor",
    ),
    path(
        "master/vendors/delete/",
        masters.delete_vendor,
        name="delete_vendor",
    ),
    path("master/process/", masters.process_master, name="process_master"),
    path(
        "master/process/update/",
        masters.update_process,
        name="master_update_process",
    ),
    path(
        "master/process/delete/",
        masters.delete_process,
        name="master_delete_process",
    ),
    path(
        "master/calibrations/completed/",
        calibration_views.completed_calibration_list,
        name="completed_calibration_list",
    ),
    path(
        "master/calibration-history/<str:tool_number>/",
        calibration_views.calibration_history_by_tool,
        name="calibration_history_by_tool",
    ),
    path(
        "master/calibration/approve/",
        calibration_views.approve_calibration,
        name="approve_calibration",
    ),
    path(
        "master/tool-fixture/edit/<str:record_id>/",
        fixture_master.tool_fixture_master_edit,
        name="tool_fixture_master_edit",
    ),
    path(
        "tool-dashboard/",
        tool_calib_dashboard.tool_dashboard,
        name="tool_dashboard",
    ),
    path("parent/table/", toolmanagement.parent_table_view, name="parent_table"),
    path("parent/add/", toolmanagement.add_parent_tool, name="add_parent"),
    path(
        "parent/update/<str:record_id>/",
        toolmanagement.update_parent_tool,
        name="update_parent_tool",
    ),
    path(
        "parent/assemble/<str:record_id>/",
        toolmanagement.parent_assemble_view,
        name="parent_assemble_view",
    ),
    path(
        "parent/assemble/save/<str:record_id>/",
        toolmanagement.assemble_parent_tool,
        name="assemble_parent_tool",
    ),
    path(
        "parent/delete/<str:parent_id>/",
        toolmanagement.delete_parent_tool,
        name="delete_parent",
    ),
    path("child/table/", toolmanagement.child_table_view, name="child_table"),
    path("child/add/", toolmanagement.add_child_tool, name="add_child"),
    path(
        "child/update/<str:record_id>/",
        toolmanagement.update_child_tool,
        name="update_child_tool",
    ),
    path(
        "child/delete/<str:child_id>/",
        toolmanagement.delete_child_tool,
        name="delete_child",
    ),
    path("coil/table/", toolmanagement.coil_table_view, name="coil_table"),
    path("coil/add/", toolmanagement.add_coil, name="add_coil"),
    path(
        "coil/update/<str:record_id>/", toolmanagement.update_coil, name="update_coil"
    ),
    path(
        "coil/items/<str:record_id>/", toolmanagement.coil_items_view, name="coil_items"
    ),
    path(
        "coil/items/add/<str:record_id>/",
        toolmanagement.add_coil_item,
        name="add_coil_item",
    ),
    path(
        "coil/items/update/<str:record_id>/",
        toolmanagement.update_coil_item,
        name="update_coil_item",
    ),
    path(
        "coil/items/delete/<str:record_id>/",
        toolmanagement.delete_coil_item,
        name="delete_coil_item",
    ),
    path("coil/delete/<str:coil_id>/", toolmanagement.delete_coil, name="delete_coil"),
    path(
        "tool-management/",
        toolmanagement.tool_management_home,
        name="tool_management_home",
    ),
    path(
        "child/items/<str:record_id>/",
        toolmanagement.child_items_view,
        name="child_items",
    ),
    path(
        "child/items/add/<str:record_id>/",
        toolmanagement.add_child_item,
        name="add_child_item",
    ),
    path(
        "child/item/update/<str:record_id>/",
        toolmanagement.update_child_item,
        name="update_child_item",
    ),
    path(
        "child/item/delete/<str:record_id>/",
        toolmanagement.delete_child_item,
        name="delete_child_item",
    ),
    # Planning Module URLs
    path("planning/", planning.planning_view, name="planning_view"),
    path(
        "upload-planning-file/",
        planning.upload_planning_file,
        name="upload_planning_file",
    ),
    path(
        "check-planning-month-upload/",
        planning.check_planning_month_upload,
        name="check_planning_month_upload",
    ),
    path(
        "remove-planning-month-upload/",
        planning.remove_planning_month_upload,
        name="remove_planning_month_upload",
    ),
    path(
        "get-planning-data/<str:planning_id>/",
        planning.get_planning_data,
        name="get_planning_data",
    ),
    path(
        "get-planning-summary/",
        planning.get_planning_summary,
        name="get_planning_summary",
    ),
    path(
        "delete-planning-data/",
        planning.delete_planning_data,
        name="delete_planning_data",
    ),
    path(
        "update-planning-row/", planning.update_planning_row, name="update_planning_row"
    ),
    path(
        "get-planning-filter/", planning.get_planning_filter, name="get_planning_filter"
    ),
    path("get-filter-options/", planning.get_filter_options, name="get_filter_options"),
    path(
        "download-planning-template/",
        planning.download_planning_template,
        name="download_planning_template",
    ),
    path(
        "get-planned-part-nos/",
        planning.get_planned_part_nos,
        name="get_planned_part_nos",
    ),
    path(
        "get-machines-for-part/",
        planning.get_machines_for_part,
        name="get_machines_for_part",
    ),
    path(
        "get-today-planned-parts/",
        planning.get_today_planned_parts,
        name="get_today_planned_parts",
    ),
    path(
        "get-machines-from-pms/",
        planning.get_machines_from_pms,
        name="get_machines_from_pms",
    ),
    path(
        "save-daily-plan/",
        planning.save_daily_plan,
        name="save_daily_plan",
    ),
    path(
        "get-daily-plan-entries/",
        planning.get_daily_plan_entries,
        name="get_daily_plan_entries",
    ),
    path(
        "update-daily-plan-machine/",
        planning.update_daily_plan_machine,
        name="update_daily_plan_machine",
    ),
    path(
        "daily-plan-machine-edit-history/",
        planning.get_daily_plan_machine_edit_history,
        name="daily_plan_machine_edit_history",
    ),
    path("email/", email_page, name="email"),
    path("email/api/rows/", email_config_rows, name="email_config_rows"),
    path("email/api/detail/", email_config_detail, name="email_config_detail"),
    path("email/api/save/", email_config_save, name="email_config_save"),
    path("settings/module/", module_settings_page, name="module_settings"),
    path(
        "settings/module/api/list/",
        module_settings_list,
        name="module_settings_list",
    ),
    path(
        "settings/module/api/toggle/",
        module_settings_toggle,
        name="module_settings_toggle",
    ),
    # Shift Settings
    path("settings/shift/", shift_settings_page, name="shift_settings"),
    path("settings/shift/api/list/", shift_settings_list, name="shift_settings_list"),
    path("settings/shift/api/save/", shift_settings_save, name="shift_settings_save"),
    path(
        "settings/shift/api/delete/",
        shift_settings_delete,
        name="shift_settings_delete",
    ),
    path(
        "settings/role-module/",
        role_module_settings_page,
        name="role_module_settings",
    ),
    path(
        "settings/role-module/api/list/",
        role_module_settings_list,
        name="role_module_settings_list",
    ),
    path(
        "settings/role-module/api/toggle/",
        role_module_settings_toggle,
        name="role_module_settings_toggle",
    ),
    # ---------- Line Master ----------
    path("line-master/", line_master_page, name="line_master"),
    # ---------- Line Monitoring Dashboard ----------
    path(
        "line-monitoring/",
        line_monitoring_project_select,
        name="line_monitoring_project_select",
    ),
    path(
        "line-monitoring/dashboard/",
        line_monitoring,
        name="line_monitoring",
    ),
    path(
        "line-monitoring/production-dashboard/",
        line_monitoring_dashboard,
        name="line_monitoring_dashboard",
    ),
    path(
        "line-monitoring/production-dashboard/api/",
        line_monitoring_dashboard_api,
        name="line_monitoring_dashboard_api",
    ),
    path("api/line-master/", line_master_list_api, name="line_master_list"),
    path("api/line-master/save/", line_master_save_api, name="line_master_save"),
    path(
        "api/line-master/<str:line_id>/update/",
        line_master_update_api,
        name="line_master_update",
    ),
    path(
        "api/line-master/<str:line_id>/delete/",
        line_master_delete_api,
        name="line_master_delete",
    ),
    # ---------- Press Machine ----------
    path(
        "pressmachine/",
        pressmachine_dashboard.press_machine_dashboard,
        name="press_machine_dashboard",
    ),
    path(
        "pressmachine/dashboards/",
        pressmachine_combined_dashboard.combined_dashboard_page,
        name="pressmachine_combined_dashboard",
    ),
    path(
        "pressmachine/daily-dashboard/",
        pressmachine_combined_dashboard.combined_dashboard_page,
        {"tab": "daily"},
        name="daily_dashboard",
    ),
    path(
        "pressmachine/daily-dashboard/api/",
        pressmachine_daily_dashboard.daily_dashboard_api,
        name="daily_dashboard_api",
    ),
    path(
        "pressmachine/report-dashboard/",
        pressmachine_combined_dashboard.combined_dashboard_page,
        {"tab": "report"},
        name="report_dashboard",
    ),
    path(
        "pressmachine/report-dashboard/api/",
        pressmachine_report_dashboard.report_dashboard_api,
        name="report_dashboard_api",
    ),
    path(
        "pressmachine/machine-dashboard/",
        pressmachine_combined_dashboard.combined_dashboard_page,
        {"tab": "machine"},
        name="press_machine_board",
    ),
    path(
        "pressmachine/machine-dashboard/api/",
        pressmachine_machine_board.machine_board_api,
        name="press_machine_board_api",
    ),
    path(
        "pressmachine/machine/<str:machine_id>/",
        pressmachine_dashboard.press_machine_detail,
        name="press_machine_detail",
    ),
    path(
        "pressmachine/api/dashboard/",
        pressmachine_dashboard.press_machine_api_dashboard,
        name="press_machine_api_dashboard",
    ),
    path(
        "pressmachine/api/filters/",
        pressmachine_dashboard.press_machine_api_filters,
        name="press_machine_api_filters",
    ),
    path(
        "pressmachine/api/machine/<str:machine_id>/",
        pressmachine_dashboard.press_machine_api_detail,
        name="press_machine_api_detail",
    ),
    path(
        "pressmachine/api/export/",
        pressmachine_dashboard.press_machine_api_export,
        name="press_machine_api_export",
    ),
    path(
        "pressmachine/firststage/",
        pressmachine_firststage.first_stage_entry,
        name="first_stage_entry",
    ),
    path(
        "pressmachine/firststage/approve/",
        pressmachine_firststage.first_stage_approve,
        name="first_stage_approve",
    ),
    path(
        "pressmachine/firststage/lookup/",
        pressmachine_firststage.lookup_work_order_master_details,
        name="work_order_master_lookup",
    ),
    path(
        "pressmachine/firststage/master/save/",
        pressmachine_firststage.work_order_master_save,
        name="work_order_master_save",
    ),
    path(
        "pressmachine/reports/",
        pressmachine_reports.press_reports_page,
        name="press_reports_page",
    ),
    path(
        "pressmachine/api/reports/filters/",
        pressmachine_reports.press_reports_api_filters,
        name="press_reports_api_filters",
    ),
    path(
        "pressmachine/api/reports/data/",
        pressmachine_reports.press_reports_api_data,
        name="press_reports_api_data",
    ),
    path(
        "pressmachine/api/reports/export/",
        pressmachine_reports.press_reports_api_export,
        name="press_reports_api_export",
    ),
    path(
        "pressmachine/downtime-reports/",
        pressmachine_downtime_reports.downtime_reports_page,
        name="downtime_reports_page",
    ),
    path(
        "pressmachine/api/downtime-reports/filters/",
        pressmachine_downtime_reports.downtime_reports_api_filters,
        name="downtime_reports_api_filters",
    ),
    path(
        "pressmachine/api/downtime-reports/data/",
        pressmachine_downtime_reports.downtime_reports_api_data,
        name="downtime_reports_api_data",
    ),
    path(
        "pressmachine/api/downtime-reports/export/",
        pressmachine_downtime_reports.downtime_reports_api_export,
        name="downtime_reports_api_export",
    ),
    path(
        "pressmachine/workorder/",
        pressmachine_workorder.workorder_list_page,
        name="workorder_list",
    ),
    path(
        "pressmachine/workorder/add/",
        pressmachine_workorder.workorder_add_page,
        name="workorder_add",
    ),
    path(
        "pressmachine/workorder/<str:workorder_id>/edit/",
        pressmachine_workorder.workorder_edit_page,
        name="workorder_edit",
    ),
    path(
        "pressmachine/api/workorders/",
        pressmachine_workorder.workorder_api_list,
        name="workorder_api_list",
    ),
    path(
        "pressmachine/api/workorders/create/",
        pressmachine_workorder.workorder_api_create,
        name="workorder_api_create",
    ),
    path(
        "pressmachine/api/workorders/<str:workorder_id>/update/",
        pressmachine_workorder.workorder_api_update,
        name="workorder_api_update",
    ),
    path(
        "pressmachine/api/workorders/<str:workorder_id>/delete/",
        pressmachine_workorder.workorder_api_delete,
        name="workorder_api_delete",
    ),
    path(
        "pressmachine/api/workorders/<str:workorder_id>/",
        pressmachine_workorder.workorder_api_detail,
        name="workorder_api_detail",
    ),
    path(
        "pressmachine/production-status-dashboard/",
        production_status_dashboard_page,
        name="production_status_dashboard",
    ),
    path(
        "pressmachine/production-status-dashboard/api/",
        production_status_dashboard_api,
        name="production_status_dashboard_api",
    ),
    path(
        "pressmachine/workorder-schedule/",
        pressmachine_workorder_schedule.workorder_schedule_page,
        name="workorder_schedule",
    ),
    path(
        "pressmachine/workorder-schedule/add/",
        pressmachine_workorder_schedule.workorder_schedule_add_page,
        name="workorder_schedule_add",
    ),
    path(
        "pressmachine/workorder-schedule/<str:workorder_id>/edit/",
        pressmachine_workorder_schedule.workorder_schedule_edit_page,
        name="workorder_schedule_edit",
    ),
]

urlpatterns = [
    *Dashboard_OEEproject,
    *extraUrls,
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
