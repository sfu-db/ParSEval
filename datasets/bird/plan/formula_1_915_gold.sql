{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "MINUS",
      "operator": "-",
      "type": "BIGINT",
      "operands": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "BIGINT"
        },
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "BIGINT"
        }
      ]
    }
  ],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [],
      "aggs": [
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [],
          "type": "BIGINT",
          "name": null
        },
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
              "type": "VARCHAR"
            }
          ],
          "type": "BIGINT",
          "name": null
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "INTEGER"
            },
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "VARCHAR",
              "operands": [
                {
                  "kind": "NOT",
                  "operator": "NOT",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "IS_NULL",
                      "operator": "IS NULL",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 3,
                          "name": "$3",
                          "type": "VARCHAR"
                        }
                      ]
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 3,
                  "name": "$3",
                  "type": "VARCHAR"
                },
                {
                  "kind": "LITERAL",
                  "value": "NULL",
                  "type": "VARCHAR",
                  "nullable": true,
                  "precision": -1
                }
              ]
            }
          ],
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "drivers",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}