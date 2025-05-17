{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 3,
      "type": "DATE"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "MINUS",
          "operator": "-",
          "type": "DECIMAL",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "DECIMAL",
              "operands": [
                {
                  "kind": "OTHER_FUNCTION",
                  "operator": "STRFTIME",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "LITERAL",
                      "value": "%Y",
                      "type": "CHAR",
                      "nullable": false,
                      "precision": 2
                    },
                    {
                      "kind": "CAST",
                      "operator": "CAST",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "CAST",
                          "operator": "CAST",
                          "type": "TIMESTAMP",
                          "operands": [
                            {
                              "kind": "OTHER_FUNCTION",
                              "operator": "CURRENT_TIMESTAMP",
                              "type": "TIMESTAMP",
                              "operands": []
                            }
                          ]
                        }
                      ]
                    }
                  ]
                }
              ]
            },
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "DECIMAL",
              "operands": [
                {
                  "kind": "OTHER_FUNCTION",
                  "operator": "STRFTIME",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "LITERAL",
                      "value": "%Y",
                      "type": "CHAR",
                      "nullable": false,
                      "precision": 2
                    },
                    {
                      "kind": "CAST",
                      "operator": "CAST",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "CAST",
                          "operator": "CAST",
                          "type": "TIMESTAMP",
                          "operands": [
                            {
                              "kind": "INPUT_REF",
                              "index": 6,
                              "name": "$6",
                              "type": "DATE"
                            }
                          ]
                        }
                      ]
                    }
                  ]
                }
              ]
            }
          ]
        },
        {
          "kind": "INPUT_REF",
          "index": 4,
          "name": "$4",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 5,
          "name": "$5",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 6,
          "name": "$6",
          "type": "DATE"
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 7,
                "name": "$7",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Japanese",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          "variableset": "[]",
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