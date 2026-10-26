# Manufacturing multipart models

This feature shipped in 0.14.0.

A Multipart Model describes a composition. Give each Part its quantity per
object, then choose **Create build** to record a particular manufacturing run.
Existing parts start at one. A build copies the composition, quantities and Model
Choices; later composition edits or deletion do not rewrite its history.

Choose how many objects to make and select a G-code Revision for each piece. A
recommended Revision is only an initial selection. Changing a recommendation
later does not change the build. You can save a build without a Revision and
